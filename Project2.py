import json
import os
import re
import ollama
import requests
import datetime
from flask import Flask, request, jsonify
from bs4 import BeautifulSoup
from selenium import webdriver
import chromedriver_autoinstaller
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from flask_ngrok import run_with_ngrok
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (
    QuickReply,
    QuickReplyButton,
    MessageAction,
    TextSendMessage,
    MessageEvent,
    TextMessage
)
from pyngrok import ngrok
from neo4j import GraphDatabase

# ===========================
# การตั้งค่าเชื่อมต่อกับ Neo4j
# ===========================
URI = "neo4j://localhost"
AUTH = ("neo4j", "Mook2024")  # เปลี่ยนรหัสผ่านตามต้องการ
driver = GraphDatabase.driver(URI, auth=AUTH) 
Model="sentence-transformers/distiluse-base-multilingual-cased-v2"
# ====================
# การตั้งค่า ngrok
# ====================
ngrok.set_auth_token("2kEJ4CQWmPX5Mf96goArSMz0Auk_283cfG7T8FkFajbgyuz4E")  # เปลี่ยนด้วยโทเคนของคุณ
public_url = ngrok.connect(5000).public_url
print(f"ngrok tunnel {public_url} -> http://127.0.0.1:5000")

# ===================================
# การตั้งค่า Chrome สำหรับการท่องเว็บแบบไม่มี GUI
# ===================================
chrome_options = webdriver.ChromeOptions()
chrome_options.add_argument('--headless')  # ปิด GUI
chrome_options.add_argument('--no-sandbox')
chrome_options.add_argument('--disable-dev-shm-usage')
chromedriver_autoinstaller.install()

# ====================
# การตั้งค่า Flask
# ====================
app = Flask(__name__)
app.config['JSON_AS_ASCII'] = False  # รองรับภาษาไทย

# ====================
# ลิงก์หมวดหมู่ของ PizzaHut
# ====================
categories = {
    "โปรโมชั่น": "https://www.pizzahut.co.th/order/deals",
    "คอมโบ": "https://www.pizzahut.co.th/order/combo",
    "พิซซ่า": "https://www.pizzahut.co.th/order/pizzas",
    "เมลทส์": "https://www.pizzahut.co.th/order/melts",
    "สปาเก็ตตี้": "https://www.pizzahut.co.th/order/pastas",
    "ของทานเล่น": "https://www.pizzahut.co.th/order/appetizers",
    "เครื่องดื่มและขนมหวาน": "https://www.pizzahut.co.th/order/drinks-&-desserts",
    "สเต๊ก": "https://www.pizzahut.co.th/order/steak",
    "ซุปและสลัด": "https://www.pizzahut.co.th/order/soup-&-salad",
    "เซ็ทไก่": "https://www.pizzahut.co.th/order/wings"
}

# ============================
# ฟังก์ชั่นสำหรับการเก็บประวัติการสนทนาใน Neo4j
# ============================
def store_chat_history(user_id, user_name, user_message, bot_response):
    try:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with driver.session() as session:
            session.run(
                """
                MERGE (u:User {id: $user_id})
                ON CREATE SET u.name = $user_name
                CREATE (c:Chat {timestamp: $timestamp, question: $question, answer: $answer})
                MERGE (u)-[:SENT]->(c)
                """,
                user_id=user_id, user_name=user_name, timestamp=timestamp,
                question=user_message, answer=bot_response
            )
        print(f"Storing chat history: {user_id}, {user_name}, {user_message}, {bot_response}")
    except Exception as e:
        print(f"Error storing chat history: {e}")


def check_chat_history(user_id, question):
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (u:User {id: $user_id})-[:SENT]->(c:Chat {question: $question})
                RETURN c.answer AS answer
                ORDER BY c.timestamp DESC
                LIMIT 1
                """,
                user_id=user_id,
                question=question
            )
            record = result.single()
            if record:
                return record["answer"]
    except Exception as e:
        print(f"Error checking chat history: {e}")
    return None

# ===================================
# ฟังก์ชั่นสำหรับการดึงข้อมูลเมนูจากเว็บไซต์ PizzaHut
# ===================================
def scrape_dishes(category_url):
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(category_url)
    dishes = []

    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, "product-item"))
        )
        soup = BeautifulSoup(driver.page_source, "html.parser")
        promo_items = soup.find_all("div", class_="product-item")
        
        for item in promo_items:
            name = item.find("div", class_="promo-item-name")
            desc = item.find("div", class_="promo-item-desc")
            price = item.find("span", class_="product-price-btn")
            
            if name and price:
                dishes.append({
                    'ชื่อเมนู': name.text.strip(),
                    'รายละเอียด': desc.text.strip() if desc else 'ไม่มีรายละเอียดเพิ่มเติม',
                    'ราคา': price.text.strip()
                })
    finally:
        driver.quit()

    return dishes

# ===================================
# ฟังก์ชั่นสำหรับการดึงข้อมูลเมนู Nobicha
# ===================================
def fetch_nobicha_menu():
    url = "https://www.nobicha.co.th/menu/"
    driver = webdriver.Chrome(options=chrome_options)
    driver.get(url)
    
    try:
        # รอให้หน้าเว็บโหลดและองค์ประกอบที่ต้องการปรากฏ
        WebDriverWait(driver, 10).until(
            EC.presence_of_all_elements_located((By.CLASS_NAME, "elementor-price-list"))
        )
        html = driver.page_source
    except Exception as e:
        print(f"Error loading Nobicha menu page: {e}")
        html = ""
    finally:
        driver.quit()
    
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    job_elements = soup.find_all("ul", {"class": "elementor-price-list"})
    
    result = []
    for job_element in job_elements:
        title_element = job_element.find("span", class_="elementor-price-list-title")
        title_price = job_element.find("span", class_="elementor-price-list-price")
        if title_element and title_price:
            result.append({
                'ชื่อเมนู': title_element.text.strip(),
                'ราคา': title_price.text.strip()
            })
            print(f"Added Nobicha dish: {title_element.text.strip()}, ราคา: {title_price.text.strip()}")
    
    return result

# ===================================
# ฟังก์ชั่นสำหรับการดึงข้อมูลคำตอบจากประวัติการสนทนา
# ===================================
def get_ollama_response(prompt, chat_history):
    # Combine prompt with chat history
    history = "\n".join(chat_history)  # รวมประวัติการสนทนา
    full_prompt = (
        f"{history}\n"
        f"User: {prompt}\n"
        f"Bot (สวมบทบาทเป็นผู้จัดการร้านพิซซ่า): "
    )

    try:
        # เพิ่มคำแนะนำในข้อความ prompt ให้ชัดเจน
        response = ollama.chat(model='supachai/llama-3-typhoon-v1.5', messages=[
            {
                'role': 'user',
                'content': full_prompt + (
                    "โปรดตอบคำถามในฐานะผู้จัดการร้านพิซซ่า โดยให้ข้อมูลอย่างชัดเจน "
                    "เป็นทางการ อ้างอิงจากแหล่งข้อมูลที่เชื่อถือได้ ห้ามเดาคำตอบ "
                    "และควรมีความกระชับ ไม่เกิน 20 คำ เป็นภาษาไทย."
                ),
            },
        ])

        # ตรวจสอบโครงสร้างข้อมูลที่ได้รับ
        if 'message' in response and 'content' in response['message']:
            return response['message']['content']
        else:
            return "ขออภัยค่ะ ฉันไม่สามารถตอบคำถามของคุณได้ในขณะนี้ โปรดลองใหม่อีกครั้งในภายหลังค่ะ"
    except Exception as e:
        return f"เกิดข้อผิดพลาดในการตอบกลับ: {str(e)}"

def return_message(line_bot_api, tk, user_id, msg):
    response = ""
    print(f"Received message: {msg}, Response: {response}")

    # ดึงข้อมูลโปรไฟล์ผู้ใช้เพื่อรับชื่อ
    try:
        profile = line_bot_api.get_profile(user_id)
        user_name = profile.display_name
    except Exception as e:
        print(f"Error retrieving user profile: {e}")
        user_name = "Unknown"

    # ดึงประวัติการสนทนาปัจจุบัน
    with driver.session() as session:
        result = session.run(
            """
            MATCH (u:User {id: $user_id})-[:SENT]->(c:Chat)
            RETURN c.question AS question, c.answer AS answer
            ORDER BY c.timestamp ASC
            """,
            user_id=user_id
        )
        chat_history = [f"User: {record['question']}\nBot: {record['answer']}" for record in result]

    # ตรวจสอบข้อความที่ได้รับและตอบกลับตามเงื่อนไข
    if msg.lower() in ["start", "เริ่มต้นใช้งาน", "hi", 'สวัสดี']:
        # ตอบกลับข้อความทักทาย
        response = f"สวัสดีค่ะ {user_name}! ฉันคือแชทบอทผู้ช่วยตอบคำถามเกี่ยวกับการสั่งพิซซ่าและเมนูอื่นๆ หากท่านมีข้อสงสัยเกี่ยวกับเมนู, การสั่งซื้อ, หรือโปรโมชั่น สามารถสอบถามมาได้เลยค่ะ ยินดีให้บริการค่ะ"

    elif msg == "โปรโมชั่น PizzaHut":
        # ส่งข้อความพร้อม Quick Reply สำหรับเลือกหมวดหมู่ต่างๆ
        response = "กรุณาเลือกหมวดหมู่ที่ต้องการดูโปรโมชั่น PizzaHut:"
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="พิซซ่า", text="พิซซ่า")),
                QuickReplyButton(action=MessageAction(label="เมลทส์", text="เมลทส์")),
                QuickReplyButton(action=MessageAction(label="สปาเก็ตตี้", text="สปาเก็ตตี้")),
                QuickReplyButton(action=MessageAction(label="ของทานเล่น", text="ของทานเล่น")),
                QuickReplyButton(action=MessageAction(label="เครื่องดื่มและขนมหวาน", text="เครื่องดื่มและขนมหวาน")),
                QuickReplyButton(action=MessageAction(label="เซ็ทไก่", text="เซ็ทไก่")),
                QuickReplyButton(action=MessageAction(label="กลับไปเลือกเมนูหลัก", text="กลับไปเลือกเมนูหลัก"))
            ]
        )
        # ส่งข้อความพร้อม Quick Reply
        line_bot_api.reply_message(tk, TextSendMessage(text=response, quick_reply=quick_reply))
        # เก็บประวัติการสนทนา
        store_chat_history(user_id, user_name, msg, response)
        return  # ออกจากฟังก์ชั่นเพื่อไม่ให้ดำเนินการต่อ

    elif msg in categories:
        category_url = categories[msg]
        dishes = scrape_dishes(category_url)

        if dishes:
            response = f"เมนู {msg}:\n"
            for dish in dishes:
                response += f"ชื่อเมนู: {dish['ชื่อเมนู']}\nรายละเอียด: {dish['รายละเอียด']}\nราคา: {dish['ราคา']}\n\n"
        else:
            response = f"ไม่พบข้อมูลเมนู {msg}"

    elif msg == "เมนู Nobicha":
        # ดึงข้อมูลเมนู Nobicha
        dishes = fetch_nobicha_menu()
        if dishes:
            response = "เมนู Nobicha:\n"
            for dish in dishes:
                response += f"ชื่อเมนู: {dish['ชื่อเมนู']}\nราคา: {dish['ราคา']}\n\n"
        else:
            response = "ไม่พบเมนูใน Nobicha"

    elif msg in ["ใช่", "ต้องการเพิ่มเติม"]:
        # ผู้ใช้ต้องการดูโปรโมชั่นเพิ่มเติม ให้แสดงเมนูหลักอีกครั้ง
        response = "กรุณาเลือกหมวดหมู่ที่ต้องการดู"
        # เพิ่ม Quick Reply สำหรับโปรโมชั่น PizzaHut และเมนู Nobicha
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="โปรโมชั่น PizzaHut", text="โปรโมชั่น PizzaHut")),
                QuickReplyButton(action=MessageAction(label="เมนู Nobicha", text="เมนู Nobicha")),
                QuickReplyButton(action=MessageAction(label="ไม่ต้องการ", text="ไม่ต้องการ"))
            ]
        )
        # ส่งข้อความพร้อม Quick Reply
        line_bot_api.reply_message(tk, TextSendMessage(text=response, quick_reply=quick_reply))
        # เก็บประวัติการสนทนา
        store_chat_history(user_id, user_name, msg, response)
        return  # ออกจากฟังก์ชั่นเพื่อไม่ให้ดำเนินการต่อ

    elif msg == "ไม่ต้องการ":
        # ผู้ใช้ไม่ต้องการอะไรเพิ่มเติม
        response = "ขอบคุณค่ะ หากท่านมีคำถามเพิ่มเติม สามารถสอบถามได้เลยค่ะ ยินดีให้บริการเสมอค่ะ"

    elif msg == "กลับไปเลือกเมนูหลัก":
        # ผู้ใช้ต้องการกลับไปยังเมนูหลัก
        response = "กรุณาเลือกหมวดหมู่หลักที่ต้องการดู:"
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="โปรโมชั่น PizzaHut", text="โปรโมชั่น PizzaHut")),
                QuickReplyButton(action=MessageAction(label="เมนู Nobicha", text="เมนู Nobicha")),
                QuickReplyButton(action=MessageAction(label="ไม่ต้องการ", text="ไม่ต้องการ"))
            ]
        )
        line_bot_api.reply_message(tk, TextSendMessage(text=response, quick_reply=quick_reply))
        store_chat_history(user_id, user_name, msg, response)
        return

    else:
        # ตรวจสอบประวัติการถามคำถามนี้
        stored_answer = check_chat_history(user_id, msg)
        if stored_answer:
            response = stored_answer
        else:
            # ไม่มีประวัติการถามคำถามนี้ ให้ใช้ Ollama ตอบคำถาม
            bot_response = get_ollama_response(msg, chat_history)
            response = bot_response

    # หลังจากตอบกลับคำถามแล้ว ให้ถามว่าต้องการอะไรเพิ่มเติมไหม
    if msg not in ["ใช่", "ไม่ต้องการ", "โปรโมชั่น PizzaHut"]:
        # สร้าง Quick Reply สำหรับการถามเพิ่มเติม
        quick_reply = QuickReply(
            items=[
                QuickReplyButton(action=MessageAction(label="โปรโมชั่น PizzaHut", text="โปรโมชั่น PizzaHut")),
                QuickReplyButton(action=MessageAction(label="เมนู Nobicha", text="เมนู Nobicha")),
                QuickReplyButton(action=MessageAction(label="ไม่ต้องการ", text="ไม่ต้องการ"))
            ]
        )
        # ส่งข้อความตอบกลับพร้อม Quick Reply
        line_bot_api.reply_message(tk, TextSendMessage(text=response, quick_reply=quick_reply))
    else:
        # ถ้าผู้ใช้ตอบว่าไม่ต้องการ ให้ส่งข้อความโดยไม่มี Quick Reply
        line_bot_api.reply_message(tk, TextSendMessage(text=response))

    # เก็บประวัติการสนทนา
    store_chat_history(user_id, user_name, msg, response)


# ===================================
# การตั้งค่า LINE Bot API และ Webhook Handler
# ===================================
@app.route("/", methods=['POST'])
def linebot():
    body = request.get_data(as_text=True)
    try:
        json_data = json.loads(body)

        # ข้อมูล Access Token และ Secret ของ LINE
        access_token = 'nJRC0h5dZhgAG5bz4oMIWCsZH4DGIqdwbDSVh80X175gyBnnEqEGmveKsfhylz7dwOxRo8DZrcAEJVVzb58Gs6jGYfTthrB7eqnMPSpCLPBfGfyhYs3NX5uNIYrKx2SG8Gj7CshKPNJNi7FRbz3aJgdB04t89/1O/w1cDnyilFU='
        secret = '9bddfe2eee25e3c5609d42ae6a996e83'

        line_bot_api = LineBotApi(access_token)
        handler = WebhookHandler(secret)
        signature = request.headers['X-Line-Signature']

        # จัดการ event จาก LINE
        handler.handle(body, signature)

        # ดึงข้อมูลข้อความจาก request
        events = json_data.get('events', [])
        for event in events:
            if event['type'] == 'message' and event['message']['type'] == 'text':
                msg = event['message']['text']
                tk = event['replyToken']
                user_id = event['source']['userId']

                # จัดการข้อความที่ได้รับ
                return_message(line_bot_api, tk, user_id, msg)

        return 'OK'

    except InvalidSignatureError:
        return 'Invalid signature. Please check your channel access token/secret.', 400
    except Exception as e:
        print(f"Error: {e}")
        print(body)

    return 'OK'


if __name__ == '__main__':
    app.run()
