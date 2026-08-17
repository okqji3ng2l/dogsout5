from datetime import datetime, timedelta
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_calendar_service():
    creds = None
    
    # 獲取當前執行腳本所在的資料夾絕對路徑
    base_path = os.path.dirname(os.path.abspath(__file__))
    
    # 將 token.json 與 client_secret.json 定義為絕對路徑
    token_path = os.path.join(base_path, 'token.json')
    client_secret_path = os.path.join(base_path, 'client_secret.json')
    
    # 1. 檢查有沒有「已登入的通行證」
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    
    # 2. 如果沒有可用憑證，讓使用者登入
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 這裡改用絕對路徑，確保不管在哪裡執行都能找到
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)
            
        # 儲存憑證供下次使用
        with open(token_path, 'w') as token:
            token.write(creds.to_json())

    return build('calendar', 'v3', credentials=creds)

# 【已修正】將 duration_hours 的預設值設為整數 1
def insert_exact_event(title, date_str, time_str="09:00", duration_hours=1, location="", description=""):
    """
    精確傳送事件到特定日期與時間
    
    :param title: 事件標題 (例如: "牙醫看診")
    :param date_str: 特定日期，格式為 "YYYY-MM-DD" (例如: "2026-08-15")
    :param time_str: 開始時間，24小時制，格式為 "HH:MM" (例如: "14:30")
    :param duration_hours: 持續時間（小時，預設為 1 小時）
    :param location: 地點 (選填)
    :param description: 備註/說明 (選填)
    """
    try:
        service = get_calendar_service()
        
        # 1. 組合日期與時間，並設定時區 (以台灣時區 Asia/Taipei 為例，即 UTC+8)
        start_time_raw = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        end_time_raw = start_time_raw + timedelta(hours=duration_hours)
        
        # 轉成 API 要求的字串格式，後面加上時區偏移量（台北為 +08:00）
        start_iso = start_time_raw.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        end_iso = end_time_raw.strftime('%Y-%m-%dT%H:%M:%S+08:00')
        
        # 2. 定義事件結構 (Event Body)
        event_body = {
            'summary': title,
            'location': location,
            'description': description,
            'start': {
                'dateTime': start_iso,
                'timeZone': 'Asia/Taipei',
            },
            'end': {
                'dateTime': end_iso,
                'timeZone': 'Asia/Taipei',
            },
        }
        
        # 3. 呼叫 insert API 寫入日曆
        event = service.events().insert(
            calendarId='primary', 
            body=event_body
        ).execute()
        
        print(f"【成功寫入】")
        print(f"事件：{event.get('summary')}")
        print(f"時間：{event['start'].get('dateTime')} 至 {event['end'].get('dateTime')}")
        print(f"連結：{event.get('htmlLink')}")

    except Exception as error:
        print(f"發生錯誤: {error}")

if __name__ == '__main__':
    insert_exact_event(
        title="專案啟動會議",
        date_str="2026-07-14",
        time_str="16:30",
        description="幹你娘"
    )