#!/usr/bin/python

import argparse
import http.client as httplib
import os
import pickle
import random
import time

import httplib2

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

# Explicitly tell the underlying HTTP transport library not to retry, since
# we are handling retry logic ourselves.
httplib2.RETRIES = 1

# Maximum number of times to retry before giving up.
MAX_RETRIES = 10

# Always retry when these exceptions are raised.
RETRIABLE_EXCEPTIONS = (httplib2.HttpLib2Error, IOError, httplib.NotConnected,
  httplib.IncompleteRead, httplib.ImproperConnectionState,
  httplib.CannotSendRequest, httplib.CannotSendHeader,
  httplib.ResponseNotReady, httplib.BadStatusLine)

# Always retry when an apiclient.errors.HttpError with one of these status
# codes is raised.
RETRIABLE_STATUS_CODES = [500, 502, 503, 504]

# 憑證與 token 都放在腳本所在目錄，避免受執行時的工作目錄影響
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRETS_FILE = os.path.join(SCRIPT_DIR, 'client_secret.json')
TOKEN_FILE = os.path.join(SCRIPT_DIR, 'token.pickle')

# 權限範圍：upload 負責上傳影片，force-ssl 負責發送留言
# 注意：修改 SCOPES 後需刪除 token.pickle 重新授權
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube.force-ssl',
]
API_SERVICE_NAME = 'youtube'
API_VERSION = 'v3'

VALID_PRIVACY_STATUSES = ('public', 'private', 'unlisted')


# Authorize the request and store authorization credentials.
def get_authenticated_service():
    creds = None
    # token.pickle 用來儲存已授權的登入狀態，避免每次都要重新開啟瀏覽器驗證
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, 'rb') as token:
            creds = pickle.load(token)

    # 如果沒有憑證或憑證失效，則啟動線上驗證流程
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 讀取你從 Google Cloud 下載的憑證 JSON 檔案
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # 儲存憑證供下次使用
        with open(TOKEN_FILE, 'wb') as token:
            pickle.dump(creds, token)

    return build(API_SERVICE_NAME, API_VERSION, credentials=creds)


def initialize_upload(youtube, options):
  tags = None
  if options.keywords:
    tags = options.keywords.split(',')

  body = dict(
    snippet=dict(
      title=options.title,
      description=options.description,
      tags=tags,
      categoryId=options.category
    ),
    status=dict(
      privacyStatus=options.privacyStatus
    )
  )

  # Call the API's videos.insert method to create and upload the video.
  insert_request = youtube.videos().insert(
    part=','.join(body.keys()),
    body=body,
    media_body=MediaFileUpload(options.file, chunksize=-1, resumable=True)
  )

  return resumable_upload(insert_request)

# This method implements an exponential backoff strategy to resume a failed upload.
def resumable_upload(request):
  response = None
  error = None
  retry = 0
  while response is None:
    try:
      print('Uploading file...')
      status, response = request.next_chunk()
      if response is not None:
        if 'id' in response:
          print('Video id "%s" was successfully uploaded.' % response['id'])
          return response['id']
        else:
          exit('The upload failed with an unexpected response: %s' % response)
    except HttpError as e:
      if e.resp.status in RETRIABLE_STATUS_CODES:
        error = 'A retriable HTTP error %d occurred:\n%s' % (e.resp.status, e.content)
      else:
        raise
    except RETRIABLE_EXCEPTIONS as e:
      error = 'A retriable error occurred: %s' % e

    if error is not None:
      print(error)
      retry += 1
      if retry > MAX_RETRIES:
        exit('No longer attempting to retry.')

      max_sleep = 2 ** retry
      sleep_seconds = random.random() * max_sleep
      print('Sleeping %f seconds and then retrying...' % sleep_seconds)
      time.sleep(sleep_seconds)


def auto_comment(youtube, video_id, comment_text):
    # 建立留言的資料結構
    body = {
        'snippet': {
            'videoId': video_id,
            'topLevelComment': {
                'snippet': {
                    'textOriginal': comment_text
                }
            }
        }
    }

    try:
        # 呼叫 YouTube API 插入留言
        response = youtube.commentThreads().insert(
            part='snippet',
            body=body
        ).execute()

        print(f"成功留言！留言 ID: {response['id']}")
    except HttpError as e:
        print(f"留言失敗: {e}")


if __name__ == '__main__':
  parser = argparse.ArgumentParser()
  parser.add_argument('--file', required=True, help='Video file to upload')
  parser.add_argument('--title', help='Video title', default='Test Title')
  parser.add_argument('--description', help='Video description', default='Test Description')
  parser.add_argument('--category', default='22',
    help='Numeric video category. See https://developers.google.com/youtube/v3/docs/videoCategories/list')
  parser.add_argument('--keywords', help='Video keywords, comma separated', default='')
  parser.add_argument('--privacyStatus', choices=VALID_PRIVACY_STATUSES,
    default='private', help='Video privacy status.')
  parser.add_argument('--comment', default='',
    help='上傳成功後自動發送的留言內容，留空則不留言')
  args = parser.parse_args()

  youtube = get_authenticated_service()

  try:
    video_id = initialize_upload(youtube, args)
  except HttpError as e:
    print('An HTTP error %d occurred:\n%s' % (e.resp.status, e.content))
  else:
    # 上傳成功後自動發送留言（共用同一組已授權的 youtube service）
    if video_id and args.comment:
      auto_comment(youtube, video_id, args.comment)
