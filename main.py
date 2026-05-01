# streamlit_app.py - Facebook Message Bot for Streamlit Cloud
# Deploy on: share.streamlit.io

import streamlit as st
import time
import json
import random
import sqlite3
import threading
import gc
import psutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import deque

from cryptography.fernet import Fernet
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

# ==================== PAGE CONFIG ====================
st.set_page_config(
    page_title="R4J M1SHR4 - Facebook Bot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ==================== CONFIGURATION ====================
SECRET_KEY = "TERI MA KI CHUT MDC"
CODE = "03102003"
MAX_TASKS = 10
MEMORY_THRESHOLD_MB = 600  # Smart restart threshold

DB_PATH = Path(__file__).parent / 'streamlit_bot.db'
ENCRYPTION_KEY_FILE = Path(__file__).parent / '.encryption_key'

# ==================== CUSTOM CSS ====================
st.markdown("""
<style>
    .main-header {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 2rem;
        border-radius: 20px;
        text-align: center;
        margin-bottom: 2rem;
    }
    .main-header h1 {
        color: white;
        font-size: 2.5rem;
        margin: 0;
    }
    .main-header p {
        color: rgba(255,255,255,0.9);
        margin-top: 0.5rem;
    }
    .stat-card {
        background: #f0f2f6;
        border-radius: 15px;
        padding: 1rem;
        text-align: center;
        border-left: 4px solid #667eea;
    }
    .stat-card .value {
        font-size: 2rem;
        font-weight: bold;
        color: #667eea;
    }
    .task-card {
        background: white;
        border-radius: 12px;
        padding: 1rem;
        margin-bottom: 1rem;
        border: 1px solid #e0e0e0;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    .running { border-left: 4px solid #28a745; }
    .stopped { border-left: 4px solid #dc3545; }
    .console-log {
        background: #1e1e1e;
        color: #00ff88;
        font-family: monospace;
        font-size: 12px;
        padding: 1rem;
        border-radius: 10px;
        height: 400px;
        overflow-y: auto;
    }
    .log-line {
        padding: 4px 8px;
        border-bottom: 1px solid #333;
        font-family: monospace;
    }
    .log-error { color: #ff6b6b; }
</style>
""", unsafe_allow_html=True)

# ==================== SESSION STATE INIT ====================
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'username' not in st.session_state:
    st.session_state.username = None
if 'tasks' not in st.session_state:
    st.session_state.tasks = {}
if 'task_logs' not in st.session_state:
    st.session_state.task_logs = {}
if 'task_threads' not in st.session_state:
    st.session_state.task_threads = {}
if 'auto_start_checked' not in st.session_state:
    st.session_state.auto_start_checked = False

# ==================== DATABASE SETUP ====================
def get_encryption_key():
    if ENCRYPTION_KEY_FILE.exists():
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        return key

ENCRYPTION_KEY = get_encryption_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_data(data):
    if not data:
        return None
    return cipher_suite.encrypt(data.encode()).decode()

def decrypt_data(encrypted_data):
    if not encrypted_data:
        return ""
    try:
        return cipher_suite.decrypt(encrypted_data.encode()).decode()
    except:
        return ""

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            cookies_encrypted TEXT,
            chat_id TEXT,
            name_prefix TEXT,
            messages TEXT,
            delay INTEGER DEFAULT 30,
            status TEXT DEFAULT 'stopped',
            messages_sent INTEGER DEFAULT 0,
            rotation_index INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    import hashlib
    cursor.execute('SELECT * FROM users WHERE username = "admin"')
    if not cursor.fetchone():
        password_hash = hashlib.sha256("admin123".encode()).hexdigest()
        cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', 
                      ('admin', password_hash))
    conn.commit()
    conn.close()

init_db()

def verify_user(username, password):
    import hashlib
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    if user and user[2] == hashlib.sha256(password.encode()).hexdigest():
        return True
    return False

def save_task_to_db(task_data):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO tasks 
        (task_id, username, cookies_encrypted, chat_id, name_prefix, messages, 
         delay, status, messages_sent, rotation_index)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        task_data['task_id'],
        task_data['username'],
        encrypt_data(json.dumps(task_data['cookies'])),
        task_data['chat_id'],
        task_data['name_prefix'],
        encrypt_data(json.dumps(task_data['messages'])),
        task_data['delay'],
        task_data['status'],
        task_data['messages_sent'],
        task_data['rotation_index']
    ))
    conn.commit()
    conn.close()

def load_tasks_from_db(username):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM tasks WHERE username = ?', (username,))
    tasks = {}
    for row in cursor.fetchall():
        try:
            cookies = json.loads(decrypt_data(row[2])) if row[2] else []
            messages = json.loads(decrypt_data(row[5])) if row[5] else []
            tasks[row[0]] = {
                'task_id': row[0],
                'username': row[1],
                'cookies': cookies,
                'chat_id': row[3] or "",
                'name_prefix': row[4] or "",
                'messages': messages,
                'delay': row[6] or 30,
                'status': row[7] or "stopped",
                'messages_sent': row[8] or 0,
                'rotation_index': row[9] or 0
            }
        except Exception as e:
            print(f"Error loading task: {e}")
    conn.close()
    return tasks

# ==================== LOGGING ====================
def log_message(task_id: str, msg: str):
    timestamp = time.strftime("%H:%M:%S")
    formatted_msg = f"[{timestamp}] {msg}"
    
    if task_id not in st.session_state.task_logs:
        st.session_state.task_logs[task_id] = deque(maxlen=100)
    
    st.session_state.task_logs[task_id].append(formatted_msg)
    print(formatted_msg)

# ==================== BROWSER SETUP ====================
def setup_browser(task_id: str):
    chrome_options = Options()
    chrome_options.add_argument('--headless=new')
    chrome_options.add_argument('--no-sandbox')
    chrome_options.add_argument('--disable-setuid-sandbox')
    chrome_options.add_argument('--disable-dev-shm-usage')
    chrome_options.add_argument('--disable-gpu')
    chrome_options.add_argument('--disable-extensions')
    chrome_options.add_argument('--window-size=1280,720')
    chrome_options.add_argument('--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
    
    # Memory optimization
    chrome_options.add_argument('--memory-pressure-off')
    chrome_options.add_argument('--max_old_space_size=128')
    chrome_options.add_argument('--js-flags="--max-old-space-size=128"')
    chrome_options.add_experimental_option('excludeSwitches', ['enable-logging'])
    
    # Streamlit cloud path
    chrome_options.binary_location = '/usr/bin/google-chrome'
    
    try:
        from selenium.webdriver.chrome.service import Service
        driver = webdriver.Chrome(options=chrome_options)
        log_message(task_id, 'Chrome started successfully!')
        return driver
    except Exception as e:
        log_message(task_id, f'Browser setup failed: {e}')
        raise e

# ==================== MESSAGE INPUT FINDER ====================
def find_message_input(driver, task_id: str, process_id: str):
    log_message(task_id, f"{process_id}: Finding message input...")
    time.sleep(5)
    
    selectors = [
        'div[contenteditable="true"][role="textbox"]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'div[aria-label*="message" i][contenteditable="true"]',
        'div[aria-label*="Message" i][contenteditable="true"]',
        '[role="textbox"][contenteditable="true"]',
        'div[contenteditable="true"]',
        'textarea',
    ]
    
    for selector in selectors:
        try:
            elements = driver.find_elements(By.CSS_SELECTOR, selector)
            for elem in elements:
                if elem.is_displayed() and elem.is_enabled():
                    elem.click()
                    time.sleep(0.5)
                    log_message(task_id, f"{process_id}: ✅ Found message input")
                    return elem
        except:
            continue
    
    log_message(task_id, f"{process_id}: ❌ Message input not found!")
    return None

# ==================== SMART RESTART CHECK ====================
def check_memory_and_restart(driver, task_data, task_id, process_id):
    """Smart restart - only if memory exceeds threshold"""
    try:
        process = psutil.Process()
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        if memory_mb > MEMORY_THRESHOLD_MB:
            log_message(task_id, f"⚠️ Memory high: {memory_mb:.0f}MB, restarting...")
            
            # Save state
            saved_rotation = task_data['rotation_index']
            saved_messages = task_data['messages_sent']
            current_chat = task_data['chat_id']
            current_cookie = task_data['cookies'][0] if task_data['cookies'] else ""
            
            # Restart browser
            driver.quit()
            time.sleep(3)
            driver = setup_browser(task_id)
            
            # Login
            driver.get('https://www.facebook.com/')
            time.sleep(5)
            
            # Add cookies
            if current_cookie:
                for cookie in current_cookie.split(';'):
                    if '=' in cookie:
                        name, value = cookie.strip().split('=', 1)
                        try:
                            driver.add_cookie({'name': name, 'value': value, 'domain': '.facebook.com'})
                        except:
                            pass
                driver.refresh()
                time.sleep(3)
            
            # Open chat
            if current_chat.startswith('https://'):
                driver.get(current_chat)
            else:
                driver.get(f'https://www.facebook.com/messages/t/{current_chat}')
            time.sleep(8)
            
            # Find message input
            message_input = find_message_input(driver, task_id, process_id)
            
            # Restore state
            task_data['rotation_index'] = saved_rotation
            task_data['messages_sent'] = saved_messages
            
            memory_after = psutil.Process().memory_info().rss / 1024 / 1024
            log_message(task_id, f"✅ Restart: {memory_mb:.0f}MB → {memory_after:.0f}MB")
            
            return driver, message_input, True
        
        return driver, None, False
        
    except Exception as e:
        log_message(task_id, f"Memory check failed: {e}")
        return driver, None, False

# ==================== MESSAGE SENDING ====================
def send_single_message(driver, message_input, task_data, task_id, process_id):
    messages_list = [msg.strip() for msg in task_data['messages'] if msg.strip()]
    if not messages_list:
        messages_list = ['Hello!']
    
    msg_idx = task_data['rotation_index'] % len(messages_list)
    base_message = messages_list[msg_idx]
    message_to_send = f"{task_data['name_prefix']} {base_message}" if task_data['name_prefix'] else base_message
    
    try:
        driver.execute_script("""
            const element = arguments[0];
            const message = arguments[1];
            element.scrollIntoView({behavior: 'smooth', block: 'center'});
            element.focus();
            element.click();
            if (element.tagName === 'DIV') {
                element.textContent = message;
                element.innerHTML = message;
            } else {
                element.value = message;
            }
            element.dispatchEvent(new Event('input', { bubbles: true }));
        """, message_input, message_to_send)
        
        time.sleep(1)
        
        # Try send button
        sent = driver.execute_script("""
            const btns = document.querySelectorAll('[aria-label*="Send" i], [data-testid="send-button"]');
            for (let btn of btns) {
                if (btn.offsetParent !== null) {
                    btn.click();
                    return true;
                }
            }
            return false;
        """)
        
        if not sent:
            message_input.send_keys("\n")
        
        task_data['messages_sent'] += 1
        task_data['rotation_index'] += 1
        save_task_to_db(task_data)
        
        log_message(task_id, f"{process_id}: ✅ Message #{task_data['messages_sent']} sent")
        return True
        
    except Exception as e:
        log_message(task_id, f"{process_id}: Send error: {str(e)[:100]}")
        return False

# ==================== TASK RUNNER ====================
def run_task(task_id: str):
    task_data = st.session_state.tasks.get(task_id)
    if not task_data:
        return
    
    process_id = f"TASK-{task_id[-6:]}"
    driver = None
    message_input = None
    
    try:
        log_message(task_id, f"{process_id}: Starting automation...")
        
        while task_data['status'] == 'running':
            # Setup browser if needed
            if driver is None:
                driver = setup_browser(task_id)
                
                # Login
                driver.get('https://www.facebook.com/')
                time.sleep(8)
                
                # Add cookies
                current_cookie = task_data['cookies'][0] if task_data['cookies'] else ""
                if current_cookie:
                    for cookie in current_cookie.split(';'):
                        if '=' in cookie:
                            name, value = cookie.strip().split('=', 1)
                            try:
                                driver.add_cookie({'name': name, 'value': value, 'domain': '.facebook.com'})
                            except:
                                pass
                    driver.refresh()
                    time.sleep(5)
                
                # Open chat (supports ID or URL)
                chat_input = task_data['chat_id']
                if chat_input.startswith('https://'):
                    driver.get(chat_input)
                else:
                    driver.get(f'https://www.facebook.com/messages/t/{chat_input}')
                time.sleep(12)
                
                # Find message input
                message_input = find_message_input(driver, task_id, process_id)
                
                if not message_input:
                    task_data['status'] = 'stopped'
                    save_task_to_db(task_data)
                    break
                
                log_message(task_id, f"{process_id}: ✅ Ready - Message #{task_data['messages_sent'] + 1}")
            
            # Send message
            success = send_single_message(driver, message_input, task_data, task_id, process_id)
            
            if success:
                # Smart restart check
                driver, new_input, restarted = check_memory_and_restart(
                    driver, task_data, task_id, process_id
                )
                if restarted and new_input:
                    message_input = new_input
                
                delay = task_data['delay']
                log_message(task_id, f"{process_id}: Waiting {delay}s...")
                
                # Wait with stop check
                for _ in range(delay):
                    if task_data['status'] != 'running':
                        break
                    time.sleep(1)
            else:
                time.sleep(10)
        
    except Exception as e:
        log_message(task_id, f"{process_id}: Fatal error: {str(e)[:100]}")
        task_data['status'] = 'stopped'
        save_task_to_db(task_data)
    
    finally:
        if driver:
            try:
                driver.quit()
            except:
                pass
        
        if task_id in st.session_state.task_threads:
            del st.session_state.task_threads[task_id]

def start_task(task_id: str):
    if task_id in st.session_state.task_threads:
        return
    
    task_data = st.session_state.tasks[task_id]
    task_data['status'] = 'running'
    save_task_to_db(task_data)
    
    thread = threading.Thread(target=run_task, args=(task_id,), daemon=True)
    thread.start()
    st.session_state.task_threads[task_id] = thread

def stop_task(task_id: str):
    if task_id in st.session_state.tasks:
        st.session_state.tasks[task_id]['status'] = 'stopped'
        save_task_to_db(st.session_state.tasks[task_id])

def delete_task(task_id: str):
    stop_task(task_id)
    if task_id in st.session_state.tasks:
        del st.session_state.tasks[task_id]
    if task_id in st.session_state.task_logs:
        del st.session_state.task_logs[task_id]
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    conn.commit()
    conn.close()

# ==================== LOGIN PAGE ====================
def login_page():
    st.markdown("""
    <div class="main-header">
        <h1>🤖 R4J M1SHR4 - Facebook Bot</h1>
        <p>Premium Message Automation Tool</p>
    </div>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1,2,1])
    with col2:
        st.markdown("### 🔐 Login")
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        
        if st.button("Login", use_container_width=True):
            if verify_user(username, password):
                st.session_state.logged_in = True
                st.session_state.username = username
                st.session_state.tasks = load_tasks_from_db(username)
                st.rerun()
            else:
                st.error("Invalid credentials! Use admin/admin123")
        
        st.markdown("---")
        st.markdown("**Default:** admin / admin123")

# ==================== MAIN APP ====================
def main_app():
    # Header
    st.markdown("""
    <div class="main-header">
        <h1>🤖 R4J M1SHR4 - Facebook Bot</h1>
        <p>Smart Memory Management | Auto Restart | 24/7 Operation</p>
    </div>
    """, unsafe_allow_html=True)
    
    # Sidebar
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.username}")
        if st.button("🚪 Logout", use_container_width=True):
            for task_id in list(st.session_state.tasks.keys()):
                stop_task(task_id)
            st.session_state.logged_in = False
            st.session_state.username = None
            st.session_state.tasks = {}
            st.session_state.task_logs = {}
            st.rerun()
        
        st.markdown("---")
        st.markdown("### ⚙️ Settings")
        st.info(f"🧠 Smart Restart: >{MEMORY_THRESHOLD_MB}MB")
        st.info(f"🔄 Browser Restart: Auto when needed")
        st.info(f"📊 Max Tasks: {MAX_TASKS}")
    
    # Create Task Form
    with st.expander("➕ Create New Task", expanded=False):
        with st.form("create_task_form"):
            col1, col2 = st.columns(2)
            with col1:
                chat_id = st.text_input("Chat ID or URL", placeholder="Numeric ID OR https://www.facebook.com/messages/e2ee/t/123")
                name_prefix = st.text_input("Name Prefix (optional)", placeholder="e.g., John")
            with col2:
                delay = st.number_input("Delay (seconds)", min_value=10, max_value=300, value=30)
                cookies = st.text_area("Facebook Cookies", placeholder="c_user=xxx; xs=xxx; datr=xxx", height=100)
            
            messages = st.text_area("Messages (one per line)", height=120, placeholder="Hello!\nHow are you?\nNice to meet you!")
            
            submitted = st.form_submit_button("🚀 Create & Start Task", use_container_width=True)
            if submitted and chat_id and cookies and messages:
                task_id = f"task_{random.randint(10000, 99999)}"
                messages_list = [m.strip() for m in messages.split('\n') if m.strip()]
                
                st.session_state.tasks[task_id] = {
                    'task_id': task_id,
                    'username': st.session_state.username,
                    'cookies': [cookies],
                    'chat_id': chat_id,
                    'name_prefix': name_prefix,
                    'messages': messages_list,
                    'delay': delay,
                    'status': 'stopped',
                    'messages_sent': 0,
                    'rotation_index': 0
                }
                save_task_to_db(st.session_state.tasks[task_id])
                start_task(task_id)
                st.success(f"✅ Task {task_id} created and started!")
                st.rerun()
    
    # Stats
    running_tasks = sum(1 for t in st.session_state.tasks.values() if t['status'] == 'running')
    total_messages = sum(t['messages_sent'] for t in st.session_state.tasks.values())
    
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.markdown(f'<div class="stat-card"><div class="value">{len(st.session_state.tasks)}</div>Total Tasks</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f'<div class="stat-card"><div class="value" style="color:#28a745;">{running_tasks}</div>Running</div>', unsafe_allow_html=True)
    with col3:
        stopped = len(st.session_state.tasks) - running_tasks
        st.markdown(f'<div class="stat-card"><div class="value" style="color:#dc3545;">{stopped}</div>Stopped</div>', unsafe_allow_html=True)
    with col4:
        st.markdown(f'<div class="stat-card"><div class="value">{total_messages}</div>Messages Sent</div>', unsafe_allow_html=True)
    
    # Tasks List
    st.markdown("### 📋 Your Tasks")
    
    if not st.session_state.tasks:
        st.info("No tasks created yet. Create one above!")
    else:
        for task_id, task_data in st.session_state.tasks.items():
            status_color = "#28a745" if task_data['status'] == 'running' else "#dc3545"
            status_text = "RUNNING" if task_data['status'] == 'running' else "STOPPED"
            
            with st.container():
                st.markdown(f"""
                <div class="task-card {'running' if task_data['status'] == 'running' else 'stopped'}">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <div>
                            <strong>🆔 {task_id}</strong>
                            <span style="color: {status_color}; margin-left: 10px;">● {status_text}</span>
                        </div>
                        <div>
                            <button onclick="alert('Use Streamlit buttons')">Action</button>
                        </div>
                    </div>
                    <div style="font-size: 0.9rem; color: #666;">
                        Chat: {task_data['chat_id'][:50]}{'...' if len(task_data['chat_id']) > 50 else ''} | 
                        Sent: {task_data['messages_sent']} msgs | 
                        Delay: {task_data['delay']}s
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                col1, col2, col3 = st.columns([1,1,4])
                with col1:
                    if task_data['status'] == 'running':
                        if st.button("⏸ Stop", key=f"stop_{task_id}"):
                            stop_task(task_id)
                            st.rerun()
                    else:
                        if st.button("▶ Start", key=f"start_{task_id}"):
                            start_task(task_id)
                            st.rerun()
                with col2:
                    if st.button("🗑 Delete", key=f"del_{task_id}"):
                        delete_task(task_id)
                        st.rerun()
                
                # Logs
                logs = st.session_state.task_logs.get(task_id, [])
                if logs:
                    with st.expander(f"📄 Logs (Last {len(logs)} entries)", expanded=False):
                        log_container = st.container()
                        with log_container:
                            for log in list(logs)[-30:]:
                                is_error = 'ERROR' in log or 'Fatal' in log or '❌' in log
                                color = "#ff6b6b" if is_error else "#00ff88"
                                st.markdown(f'<div style="font-family: monospace; font-size: 11px; color: {color}; padding: 2px 0;">{log}</div>', unsafe_allow_html=True)
                
                st.markdown("<hr style='margin: 0.5rem 0;'>", unsafe_allow_html=True)
    
    # Auto-start tasks that were running
    if not st.session_state.auto_start_checked:
        st.session_state.auto_start_checked = True
        for task_id, task_data in st.session_state.tasks.items():
            if task_data['status'] == 'running':
                start_task(task_id)

# ==================== MAIN ====================
if not st.session_state.logged_in:
    login_page()
else:
    main_app()

st.markdown("""
<div style="text-align: center; padding: 2rem; color: #666; font-size: 0.8rem;">
    Made with ❤️ by R4J M1SHR4 | Smart Memory Management
</div>
""", unsafe_allow_html=True)
