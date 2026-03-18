# audio_overview.py
import asyncio
import json
import logging
import os
from datetime import datetime

import edge_tts

logger = logging.getLogger(__name__)


class AudioOverviewGenerator:
    """
    Generates an 'Audio Overview' (Podcast) from text content.
    Uses edge-tts for high-quality speech synthesis.
    """

    VOICE_HOST_A = "zh-CN-XiaoxiaoNeural"  # Female, lively
    VOICE_HOST_B = "zh-CN-YunxiNeural"  # Male, steady

    def __init__(self, output_dir="static/audio_cache"):
        self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    async def generate_script(self, text_content, client_model):
        """
        Generates a podcast script from the given text content using the AI model.
        Returns a list of dicts: [{'speaker': 'Host A', 'text': '...'}, ...]
        """
        prompt = f"""
        You are an expert podcast producer. I will provide you with a source text.
        Your task is to convert this text into a lively, engaging podcast dialogue between two hosts:
        - Host A (Female): Enthusiastic, curious, leads the conversation.
        - Host B (Male): Knowledgeable, calm, provides deep insights.

        Rules:
        1. The dialogue should be natural, including realistic filler words (like "wow", "I see", "exactly") and interruptions where appropriate.
        2. Do NOT just read the text summary. Make it a conversation.
        3. Explain complex topics simply.
        4. Keep it engaging and fun.
        5. Output ONLY a valid JSON array of objects. Each object must have "speaker" ("Host A" or "Host B") and "text" (the spoken line).
        6. The language must be Chinese (Mandarin).

        Source Text:
        {text_content[:15000]}  # Limit context to avoid token limits if necessary
        
        Strict JSON Output format:
        [
            {{"speaker": "Host A", "text": "大家好，欢迎来到今天的 Deep Dive！"}},
            {{"speaker": "Host B", "text": "大家好，今天我们要聊的内容非常有意思。"}}
        ]
        """

        try:
            response = client_model.generate_content(prompt)
            # Clean up potential markdown code blocks
            res_text = response.text.strip()
            if res_text.startswith("```json"):
                res_text = res_text[7:-3].strip()
            elif res_text.startswith("```"):
                res_text = res_text[3:-3].strip()

            script = json.loads(res_text)
            return script
        except Exception as e:
            logger.info(f"Error generating script: {e}")
            return None

    async def synthesize_audio(self, script, session_id):
        """
        Synthesizes audio for each script line and combines them.
        Since ffmpeg might be missing, we will use direct binary appending for MP3s (works often)
        or return a playlist. Here we try binary append for a single file experience.
        """
        combined_audio_path = os.path.join(self.output_dir, f"podcast_{session_id}.mp3")
        temp_files = []

        try:
            with open(combined_audio_path, "wb") as outfile:
                for idx, line in enumerate(script):
                    speaker = line.get("speaker")
                    text = line.get("text")
                    voice = (
                        self.VOICE_HOST_A if speaker == "Host A" else self.VOICE_HOST_B
                    )

                    temp_file = os.path.join(
                        self.output_dir, f"temp_{session_id}_{idx}.mp3"
                    )
                    communicate = edge_tts.Communicate(text, voice)
                    await communicate.save(temp_file)
                    temp_files.append(temp_file)

                    # Append binary content
                    with open(temp_file, "rb") as infile:
                        outfile.write(infile.read())

            return combined_audio_path
        except Exception as e:
            logger.info(f"Error synthesizing audio: {e}")
            return None
        finally:
            # Cleanup temp files
            for f in temp_files:
                if os.path.exists(f):
                    try:
                        os.remove(f)
                    except OSError as e:
                        logger.debug("Failed to remove temp file %s: %s", f, e)
