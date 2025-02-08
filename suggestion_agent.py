import os
import re
from typing import Dict, List

import requests
from dotenv import load_dotenv


class SuggestionAgent:
    def __init__(self):
        load_dotenv()
        self.api_key = os.getenv("ANTHROPIC_API_KEY")

        # Seyahat ile ilgili kalıplar
        self.patterns = {
            "seyahat_amaci": [r"tatil", r"iş", r"ziyaret", r"gezi"],
            "destinasyon": [r"nere", r"ülke", r"şehir", r"rota"],
            "tarih": [r"tarih", r"zaman", r"sezon", r"ne zaman"],
            "butce": [r"bütçe", r"fiyat", r"maliyet", r"ücret"],
            "konaklama": [r"otel", r"konaklama", r"apart", r"pansiyon"],
            "ulasim": [r"uçuş", r"transfer", r"ulaşım", r"araç"],
            "genel": [r".*"],  # Hiçbir kalıp uymadığında genel öneri üretmek için
        }

    def call_claude(self, prompt: str) -> str:
        """Claude API'yi çağırır"""
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        body = {
            "model": "claude-3-5-sonnet-20240620",
            "max_tokens": 1000,
            "temperature": 0.7,
            "messages": [{"role": "user", "content": prompt}],
        }

        try:
            response = requests.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=body
            )
            return response.json()["content"][0]["text"]
        except Exception as e:
            print(f"Claude API error: {e}")
            return ""

    def detect_context(self, message: str, conversation_history: List[Dict]) -> Dict:
        """Mesajın bağlamını tespit eder"""
        message = message.lower()
        context = {"type": "genel", "related_info": {}}

        # Son kullanıcı mesajı ve bot yanıtını al
        last_user_msg = next(
            (
                msg["content"]
                for msg in reversed(conversation_history)
                if msg["role"] == "user"
            ),
            "",
        )
        last_bot_msg = next(
            (
                msg["content"]
                for msg in reversed(conversation_history)
                if msg["role"] == "assistant"
            ),
            "",
        )

        # Bağlam tespiti
        for context_type, patterns in self.patterns.items():
            if any(re.search(pattern, message) for pattern in patterns):
                context["type"] = context_type
                break

        # İlgili bilgileri topla
        context["related_info"] = {
            "last_user_msg": last_user_msg,
            "last_bot_msg": last_bot_msg,
            "numbers": self._extract_amounts(message + " " + last_user_msg),
        }

        return context

    def generate_suggestions(
        self, bot_message: str, conversation_history: List[Dict]
    ) -> List[str]:
        """Her mesaj için bağlama uygun öneriler üretir"""
        context = self.detect_context(bot_message, conversation_history)

        prompt = self._create_contextual_prompt(bot_message, context)

        try:
            response = self.call_claude(prompt)
            suggestions = self.parse_suggestions(response)
            return suggestions[:4]  # En fazla 4 öneri göster
        except Exception as e:
            print(f"Error generating suggestions: {e}")
            return self._get_fallback_suggestions(context["type"])

    def _create_contextual_prompt(self, message: str, context: Dict) -> str:
        """Bağlama özel prompt oluşturur"""
        base_prompt = """
        Son bot mesajı: {message}

        Bu mesaja uygun 3-4 muhtemel kullanıcı yanıtı öner.
        
        Önemli kurallar:
        1. Her seferinde sadece tek bir soru sor
        2. Kısa ve net cevaplar ver (maksimum 2-3 cümle)
        3. Seyahat planlamasını adım adım yap
        4. Her adımda sadece gerekli bilgiyi iste
        
        Değerler:
        - Uçuş fiyatları: 2.000 TL - 50.000 TL arası
        - Otel fiyatları: 1.500 TL - 15.000 TL/gece
        - Transfer ücretleri: 500 TL - 2.000 TL
        - Seyahat süreleri: 2-14 gün arası
        
        Yanıtlar:
        - Tek kelime veya kısa ifadeler olmalı
        - Her biri yeni satırda ve tire (-) ile başlamalı
        - Seyahat bağlamına uygun olmalı
        
        Bağlam bilgileri:
        - Tip: {context_type}
        - Son kullanıcı mesajı: {last_user_msg}
        
        Öneriler:
        """

        return base_prompt.format(
            message=message,
            context_type=context["type"],
            last_user_msg=context["related_info"].get("last_user_msg", ""),
        )

    def parse_suggestions(self, response: str) -> List[str]:
        """Claude yanıtından önerileri ayıklar"""
        suggestions = []

        for line in response.split("\n"):
            line = line.strip()
            if line.startswith("-"):
                suggestion = line.replace("-", "").strip()
                if suggestion and len(suggestions) < 4:  # En fazla 4 öneri
                    suggestions.append(suggestion)

        return suggestions or self._get_fallback_suggestions("genel")

    def _get_fallback_suggestions(self, context_type: str) -> List[str]:
        """API hatası durumunda yedek öneriler"""
        fallbacks = {
            "seyahat_amaci": ["İş seyahati", "Tatil", "Aile ziyareti", "Kültür turu"],
            "destinasyon": ["Avrupa", "Uzak Doğu", "Amerika", "Orta Doğu"],
            "tarih": ["Yaz sezonu", "Kış sezonu", "Bahar ayları", "Sonbahar"],
            "butce": ["Ekonomik paket", "Orta segment", "Lüks seyahat", "Ultra lüks"],
            "konaklama": [
                "5 yıldızlı otel",
                "Butik otel",
                "Apart otel",
                "Ekonomik otel",
            ],
            "ulasim": ["Direkt uçuş", "Aktarmalı uçuş", "VIP transfer", "Toplu taşıma"],
            "genel": ["Evet", "Hayır", "Detaylı bilgi alabilir miyim?", "Devam edelim"],
        }
        return fallbacks.get(context_type, fallbacks["genel"])

    def _extract_amounts(self, text: str) -> List[int]:
        """Metinden tüm sayısal değerleri çıkarır"""
        numbers = re.findall(r"\d+(?:,\d+)*(?:\.\d+)?", text.replace(".", ""))
        return [int(float(num.replace(",", ""))) for num in numbers]
