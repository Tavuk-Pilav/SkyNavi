import json
import os
import random
import sqlite3
import time
from datetime import datetime, timedelta
from uuid import uuid4

import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session
from markdown import markdown

from suggestion_agent import SuggestionAgent

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Load environment variables
load_dotenv()


def get_db():
    """Veritabanı bağlantısı oluşturur"""
    conn = sqlite3.connect("conversations.db")
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Veritabanı tablolarını oluşturur"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                session_id TEXT PRIMARY KEY,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """
        )
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                role TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES conversations (session_id)
                ON DELETE CASCADE
            )
        """
        )
        conn.commit()


def get_conversation_history(session_id):
    """Belirli bir session'a ait konuşma geçmişini getirir"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "SELECT role, content FROM messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            )
            return [
                {"role": row["role"], "content": row["content"]} for row in c.fetchall()
            ]
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []


def get_all_conversations():
    """Tüm konuşmaların listesini getirir"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                """
                SELECT 
                    c.session_id, 
                    c.title, 
                    c.created_at,
                    COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON c.session_id = m.session_id
                GROUP BY c.session_id
                ORDER BY c.created_at DESC
            """
            )
            return [
                {
                    "id": row["session_id"],
                    "title": row["title"],
                    "created_at": datetime.strptime(
                        row["created_at"], "%Y-%m-%d %H:%M:%S"
                    ).strftime("%d.%m.%Y %H:%M"),
                    "message_count": row["message_count"],
                }
                for row in c.fetchall()
            ]
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return []


def save_message(session_id, role, content):
    """Yeni mesajı veritabanına kaydeder"""
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )
            conn.commit()
            return True
    except sqlite3.Error as e:
        print(f"Database error: {e}")
        return False


def call_claude(messages):
    """Claude API'yi çağırır ve yanıt alır"""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not found in environment variables")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    url = "https://api.anthropic.com/v1/messages"

    body = {
        "model": "claude-3-5-sonnet-20240620",
        "max_tokens": 4096,
        "messages": messages,
        "temperature": 0.7,
        "system": """Sen SkyNavi'nin seyahat asistanısın. THY ve partner havayollarının uçuş verilerine, otel rezervasyon sistemlerine ve transfer servislerine erişebilen bir dijital seyahat danışmanısın.

        İletişim Kuralları:
        1. Her yanıtında sadece bir soru sor
        2. Yanıtların 3-4 cümleyi geçmesin
        3. Önemli bilgileri vurgula
        4. Gereksiz nezaket cümleleri kurma
        5. Her adımda mantıklı bir sıra izle

        Seyahat Planlama Adımları:
        1. Amaç Belirleme
           - Seyahat amacı (İş/Tatil/Ziyaret)
           - Seyahat stili (Lüks/Ekonomik/Macera)
           - Özel gereksinimler

        2. Destinasyon ve Tarih
           - Yurt içi/Yurt dışı tercihi
           - Hedef şehir/bölge
           - Seyahat tarihi/sezonu
           - Kalış süresi

        3. Bütçe ve Konaklama
           - Toplam bütçe aralığı
           - Konaklama tipi tercihi
           - Otel konumu önceliği
           - Oda tipi tercihi

        4. Ulaşım Detayları
           - Uçuş tercihi (Direkt/Aktarmalı)
           - Transfer ihtiyacı
           - Araç kiralama talebi
           - Miles&Smiles kullanımı

        Her yanıtında:
        1. Kullanıcının verdiği bilgiyi özetle
        2. Hangi planlama adımında olduğunu belirt
        3. O adıma uygun tek bir soru sor
        4. Varsa önemli notları ekle (vize, sezon vb.)""",
    }

    max_retries = 5
    retry_delay = 1

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=body)
            response.raise_for_status()
            return response.json()["content"][0]["text"]
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise


def process_query(query, user_data, session_id):
    """Kullanıcı sorgusunu işler ve yanıt üretir"""
    try:
        conversation_history = get_conversation_history(session_id)

        # Analiz yap
        analysis = {}
        if "harcama" in query.lower():
            analysis["spending"] = analyze_spending_patterns(user_data)
        if "tahmin" in query.lower():
            analysis["prediction"] = predict_next_month(user_data)
        if "tasarruf" in query.lower():
            analysis["savings"] = calculate_savings_potential(user_data)

        enriched_query = (
            f"{query}\n\nFinansal Analiz: {json.dumps(analysis, indent=2)}"
            if analysis
            else query
        )

        # Claude API'ye gönderilecek mesajları hazırla
        messages = conversation_history + [{"role": "user", "content": enriched_query}]

        # Claude'dan yanıt al
        response = call_claude(messages)

        # Mesajları kaydet
        if not save_message(session_id, "user", enriched_query):
            raise Exception("Failed to save user message")
        if not save_message(session_id, "assistant", response):
            raise Exception("Failed to save assistant message")

        return response
    except Exception as e:
        print(f"Error processing query: {e}")
        return "Üzgünüm, bir hata oluştu. Lütfen tekrar deneyin."


def analyze_spending_patterns(user_data):
    """Harcama paternlerini analiz eder"""
    total_spending = sum(user_data["harcama"])
    avg_spending = total_spending / len(user_data["harcama"])
    spending_trend = [
        y - x for x, y in zip(user_data["harcama"][:-1], user_data["harcama"][1:])
    ]
    return {
        "total": total_spending,
        "average": avg_spending,
        "trend": "artıyor" if sum(spending_trend) > 0 else "azalıyor",
    }


def predict_next_month(user_data):
    """Gelecek ay için harcama tahmini yapar"""
    last_3_months = user_data["harcama"][-3:]
    predicted = sum(last_3_months) / len(last_3_months)
    return predicted * (1 + random.uniform(-0.1, 0.1))


def calculate_savings_potential(user_data):
    """Tasarruf potansiyelini hesaplar"""
    income = sum(user_data["gelir"])
    expenses = sum(user_data["harcama"])
    return {
        "current_savings": income - expenses,
        "potential_savings": (income - expenses) * 1.2,
        "savings_ratio": (income - expenses) / income * 100,
    }


# Sample user data
user_data = {
    "tarih": ["2024-01", "2024-02", "2024-03"],
    "gelir": [12000, 12000, 12000],
    "harcama": [9000, 10500, 8500],
    "kategori": ["market", "eğlence", "faturalar"],
}

# Initialize the database
init_db()


def renderMarkdown(text):
    return markdown(text, extensions=["fenced_code", "tables"])


@app.route("/")
def home():
    """Ana sayfa"""
    if "session_id" not in session:
        session["session_id"] = str(uuid4())
        try:
            with get_db() as conn:
                c = conn.cursor()
                c.execute(
                    "INSERT INTO conversations (session_id, title) VALUES (?, ?)",
                    (session["session_id"], "Yeni Konuşma"),
                )
                conn.commit()
        except sqlite3.Error as e:
            print(f"Database error: {e}")
            return "Bir hata oluştu", 500

    conversations = get_all_conversations()
    conversation_history = get_conversation_history(session["session_id"])
    return render_template(
        "index.html",
        conversation_history=conversation_history,
        conversations=conversations,
        current_session=session["session_id"],
        renderMarkdown=renderMarkdown,
    )


@app.route("/conversation/<session_id>")
def load_conversation(session_id):
    """Belirli bir konuşmayı yükler"""
    try:
        session["session_id"] = session_id
        conversation_history = get_conversation_history(session_id)
        conversations = get_all_conversations()
        return render_template(
            "index.html",
            conversation_history=conversation_history,
            conversations=conversations,
            current_session=session_id,
            renderMarkdown=renderMarkdown,
        )
    except Exception as e:
        print(f"Error loading conversation: {e}")
        return "Konuşma yüklenirken bir hata oluştu", 500


@app.route("/new_chat", methods=["POST"])
def new_chat():
    """Yeni bir konuşma başlatır"""
    try:
        session["session_id"] = str(uuid4())
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "INSERT INTO conversations (session_id, title) VALUES (?, ?)",
                (session["session_id"], "Yeni Konuşma"),
            )
            conn.commit()
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error creating new chat: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/send_message", methods=["POST"])
def send_message():
    """Yeni mesaj gönderir"""
    try:
        if "session_id" not in session:
            raise ValueError("No active session")

        user_message = request.json.get("message", "").strip()
        if not user_message:
            raise ValueError("Empty message")

        # İlk mesajsa, konuşma başlığını güncelle
        with get_db() as conn:
            c = conn.cursor()
            c.execute(
                "UPDATE conversations SET title = ? WHERE session_id = ? AND title = ?",
                (user_message[:50], session["session_id"], "Yeni Konuşma"),
            )
            conn.commit()

        # Mesajı işle ve yanıt al
        response = process_query(user_message, user_data, session["session_id"])

        # Konuşma geçmişini al
        conversation_history = get_conversation_history(session["session_id"])

        # Akıllı öneriler üret
        suggestion_agent = SuggestionAgent()
        suggestions = suggestion_agent.generate_suggestions(
            response, conversation_history
        )

        return jsonify(
            {
                "response": response,
                "suggestions": suggestions,
                "conversation_history": conversation_history,
                "conversations": get_all_conversations(),
            }
        )
    except Exception as e:
        print(f"Error sending message: {e}")
        return (
            jsonify(
                {"error": "Mesaj gönderilirken bir hata oluştu", "details": str(e)}
            ),
            500,
        )


if __name__ == "__main__":
    app.run(debug=True)
