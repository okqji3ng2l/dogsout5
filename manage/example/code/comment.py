import os
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle

# 設定權限範圍（留言需要 force-ssl 權限）
SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']

def get_authenticated_service():
    creds = None
    # token.pickle 用來儲存已授權的登入狀態，避免每次都要重新開啟瀏覽器驗證
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
            
    # 如果沒有憑證或憑證失效，則啟動線上驗證流程
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 讀取你從 Google Cloud 下載的憑證 JSON 檔案
            flow = InstalledAppFlow.from_client_secrets_file('/mnt/d/dogsout/manage/example/code/client_secret.json', SCOPES)
            creds = flow.run_local_server(port=0)
            
        # 儲存憑證供下次使用
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    return build('youtube', 'v3', credentials=creds)

def auto_comment(video_id, comment_text):
    youtube = get_authenticated_service()
    
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
    except Exception as e:
        print(f"發生錯誤: {e}")

if __name__ == '__main__':
    # 填入你想留言的 YouTube 影片 ID（網址 v= 後面的那一串字串）
    TARGET_VIDEO_ID = 'lmCVIQn3Kbw' 
    MY_COMMENT = '這是一則透過 API 自動發送的測試留言'
    
    auto_comment(TARGET_VIDEO_ID, MY_COMMENT)