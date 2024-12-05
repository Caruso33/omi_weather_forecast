from flask import Flask, request, jsonify
import logging
import time
import os
from collections import defaultdict
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from pathlib import Path
from datetime import datetime, timedelta
import threading
import requests
from dotenv import load_dotenv

load_dotenv()


client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

app = Flask(__name__)

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Modify trigger phrases and add buffer for partial triggers
TRIGGER_PHRASES = ["hey omi", "hey, omi"]  # Base triggers
PARTIAL_FIRST = ["hey", "hey,"]  # First part of trigger
PARTIAL_SECOND = ["omi"]  # Second part of trigger
QUESTION_AGGREGATION_TIME = 5  # seconds to wait for collecting the question


# Replace the message buffer with a class to better manage state
class MessageBuffer:
    def __init__(self):
        self.buffers = {}
        self.lock = threading.Lock()
        self.cleanup_interval = 300  # 5 minutes
        self.last_cleanup = time.time()

    def get_buffer(self, session_id):
        current_time = time.time()

        # Cleanup old sessions periodically
        if current_time - self.last_cleanup > self.cleanup_interval:
            self.cleanup_old_sessions()

        with self.lock:
            if session_id not in self.buffers:
                self.buffers[session_id] = {
                    "messages": [],
                    "trigger_detected": False,
                    "trigger_time": 0,
                    "collected_question": [],
                    "response_sent": False,
                    "partial_trigger": False,
                    "partial_trigger_time": 0,
                    "last_activity": current_time,
                }
            else:
                self.buffers[session_id]["last_activity"] = current_time

        return self.buffers[session_id]

    def cleanup_old_sessions(self):
        current_time = time.time()
        with self.lock:
            expired_sessions = [
                session_id
                for session_id, data in self.buffers.items()
                if current_time - data["last_activity"]
                > 3600  # Remove sessions older than 1 hour
            ]
            for session_id in expired_sessions:
                del self.buffers[session_id]
            self.last_cleanup = current_time


# Replace the message_buffer defaultdict with our new class
message_buffer = MessageBuffer()

# Add cooldown tracking
notification_cooldowns = defaultdict(float)
NOTIFICATION_COOLDOWN = 10  # 10 seconds cooldown between notifications for each session

# Add these near the top of the file, after the imports
if os.getenv("HTTPS_PROXY"):
    os.environ["OPENAI_PROXY"] = os.getenv("HTTPS_PROXY")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_openai_response(text):
    """Get response from OpenAI for the user's question"""
    try:
        logger.info(f"Sending question to OpenAI: {text}")

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {
                    "role": "system",
                    "content": "You are Omi, a helpful AI assistant. Provide clear, concise, and friendly responses.",
                },
                {"role": "user", "content": text},
            ],
            temperature=0.7,
            max_tokens=150,
            timeout=30,
        )

        answer = response.choices[0].message.content.strip()
        logger.info(f"Received response from OpenAI: {answer}")
        return answer
    except Exception as e:
        logger.error(f"Error getting OpenAI response: {str(e)}")
        return "I'm sorry, I encountered an error processing your request."


@app.route("/webhook", methods=["POST"])
def webhook():
    if request.method == "POST":
        logger.info("Received webhook POST request")
        data = request.json
        logger.info(f"Received data: {data}")

        session_id = data.get("session_id")
        uid = request.args.get("uid")
        logger.info(f"Processing request for session_id: {session_id}, uid: {uid}")

        if not session_id:
            logger.error("No session_id provided in request")
            return (
                jsonify({"status": "error", "message": "No session_id provided"}),
                400,
            )

        current_time = time.time()
        buffer_data = message_buffer.get_buffer(session_id)
        segments = data.get("segments", [])
        has_processed = False

        # Add debug logging
        logger.debug(f"Current buffer state for session {session_id}: {buffer_data}")

        # Only check cooldown if we have a trigger and are about to process
        if buffer_data["trigger_detected"] and not buffer_data["response_sent"]:
            time_since_last_notification = (
                current_time - notification_cooldowns[session_id]
            )
            if time_since_last_notification < NOTIFICATION_COOLDOWN:
                logger.info(
                    f"Cooldown active. {NOTIFICATION_COOLDOWN - time_since_last_notification:.0f}s remaining"
                )
                return jsonify({"status": "success"}), 200

        # Process each segment
        for segment in segments:
            if not segment.get("text") or has_processed:
                continue

            text = segment["text"].lower().strip()
            logger.info(f"Processing text segment: '{text}'")

            # Check for complete trigger phrases first
            if (
                any(trigger in text for trigger in [t.lower() for t in TRIGGER_PHRASES])
                and not buffer_data["trigger_detected"]
            ):
                logger.info(f"Complete trigger phrase detected in session {session_id}")
                buffer_data["trigger_detected"] = True
                buffer_data["trigger_time"] = current_time
                buffer_data["collected_question"] = []
                buffer_data["response_sent"] = False
                buffer_data["partial_trigger"] = False
                notification_cooldowns[session_id] = (
                    current_time  # Set cooldown when trigger is detected
                )

                # Extract any question part that comes after the trigger
                question_part = (
                    text.split("omi,")[-1].strip() if "omi," in text.lower() else ""
                )
                if question_part:
                    buffer_data["collected_question"].append(question_part)
                    logger.info(
                        f"Collected question part from trigger: {question_part}"
                    )
                continue

            # Check for partial triggers
            if not buffer_data["trigger_detected"]:
                # Check for first part of trigger
                if any(text.endswith(part.lower()) for part in PARTIAL_FIRST):
                    logger.info(
                        f"First part of trigger detected in session {session_id}"
                    )
                    buffer_data["partial_trigger"] = True
                    buffer_data["partial_trigger_time"] = current_time
                    continue

                # Check for second part if we're waiting for it
                if buffer_data["partial_trigger"]:
                    time_since_partial = (
                        current_time - buffer_data["partial_trigger_time"]
                    )
                    if (
                        time_since_partial <= 2.0
                    ):  # 2 second window to complete the trigger
                        if any(part.lower() in text.lower() for part in PARTIAL_SECOND):
                            logger.info(
                                f"Complete trigger detected across segments in session {session_id}"
                            )
                            buffer_data["trigger_detected"] = True
                            buffer_data["trigger_time"] = current_time
                            buffer_data["collected_question"] = []
                            buffer_data["response_sent"] = False
                            buffer_data["partial_trigger"] = False

                            # Extract any question part that comes after "omi"
                            question_part = (
                                text.split("omi,")[-1].strip()
                                if "omi," in text.lower()
                                else ""
                            )
                            if question_part:
                                buffer_data["collected_question"].append(question_part)
                                logger.info(
                                    f"Collected question part from second trigger part: {question_part}"
                                )
                            continue
                    else:
                        # Reset partial trigger if too much time has passed
                        buffer_data["partial_trigger"] = False

            # If trigger was detected, collect the question
            if (
                buffer_data["trigger_detected"]
                and not buffer_data["response_sent"]
                and not has_processed
            ):
                time_since_trigger = current_time - buffer_data["trigger_time"]
                logger.info(f"Time since trigger: {time_since_trigger} seconds")

                if time_since_trigger <= QUESTION_AGGREGATION_TIME:
                    buffer_data["collected_question"].append(text)
                    logger.info(f"Collecting question part: {text}")
                    logger.info(
                        f"Current collected question: {' '.join(buffer_data['collected_question'])}"
                    )

                # Check if we should process the question
                should_process = (
                    (
                        time_since_trigger > QUESTION_AGGREGATION_TIME
                        and buffer_data["collected_question"]
                    )
                    or (buffer_data["collected_question"] and "?" in text)
                    or (time_since_trigger > QUESTION_AGGREGATION_TIME * 1.5)
                )

                if should_process and buffer_data["collected_question"]:
                    # Process question and send response
                    full_question = " ".join(buffer_data["collected_question"]).strip()
                    if not full_question.endswith("?"):
                        full_question += "?"

                    logger.info(f"Processing complete question: {full_question}")

                    # Use OpenAI to extract city and country
                    location_query = f"Extract the city and country from the following question, comma separate it, it should be the only thing returned: {full_question}"
                    location_response = get_openai_response(location_query)
                    logger.info(f"Extracted location: {location_response}")

                    # Call the weather forecast function
                    location_parts = location_response.split(",")
                    if len(location_parts) == 2:
                        city, country = (
                            location_parts[0].strip(),
                            location_parts[1].strip(),
                        )
                        forecast_response = get_weather_forecast(city, country)
                        logger.info(f"Weather forecast: {forecast_response}")

                        # Reset all states
                        buffer_data["trigger_detected"] = False
                        buffer_data["trigger_time"] = 0
                        buffer_data["collected_question"] = []
                        buffer_data["response_sent"] = True
                        buffer_data["partial_trigger"] = False
                        has_processed = True

                        return jsonify({"message": forecast_response}), 200
                    else:
                        logger.error(
                            "Failed to extract valid city and country from the question"
                        )
                        return (
                            jsonify(
                                {
                                    "status": "error",
                                    "message": "Invalid location extracted",
                                }
                            ),
                            400,
                        )

        # Return success if no response needed
        return jsonify({"status": "success"}), 200


@app.route("/webhook/setup-status", methods=["GET"])
def setup_status():
    try:
        # Always return true for setup status
        return jsonify({"is_setup_completed": True}), 200
    except Exception as e:
        logger.error(f"Error checking setup status: {str(e)}")
        return jsonify({"is_setup_completed": False, "error": str(e)}), 500


@app.route("/status", methods=["GET"])
def status():
    return jsonify(
        {
            "active_sessions": len(message_buffer.buffers),
            "uptime": time.time() - start_time,
        }
    )


# Add at the top of the file with other globals
start_time = time.time()


@app.route("/weather", methods=["GET"])
def get_weather():
    location = request.args.get("location")

    if not location:
        return jsonify({"error": "Location is required"}), 400

    try:
        # Split the location into city and country if possible
        location_parts = location.split(",")
        if len(location_parts) == 2:
            city, country = location_parts[0].strip(), location_parts[1].strip()
            forecast_response = get_weather_forecast(city, country)

            return jsonify({"forecast": forecast_response})
        else:
            return (
                jsonify({"error": "Invalid location format. Use 'City, Country'"}),
                400,
            )

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def generate_forecast_text(daily_forecast):
    # Convert the forecast response into a nice text using OpenAI
    prompt = f"Convert the following forecast data into a friendly text which will be read: {daily_forecast}"
    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {
                "role": "system",
                "content": "You are Omi, a helpful AI assistant. Provide clear, concise, and friendly responses.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=250,
        timeout=30,
    )

    forecast_text = response.choices[0].message.content.strip()
    return forecast_text


# Function to get weather forecast based on city and country
def get_weather_forecast(city, country):
    try:
        geocode_response = requests.get(
            f'https://maps.googleapis.com/maps/api/geocode/json?address={city},{country}&key={os.getenv("GOOGLE_GEOCODING_API_KEY")}'
        )

        geocode_data = geocode_response.json()
        results = geocode_data.get("results", [])

        if not results:
            return "Location not found"

        geometry = results[0]["geometry"]
        lat = geometry["location"]["lat"]
        lng = geometry["location"]["lng"]

        weather_response = requests.get(
            f'https://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={lng}&units=metric&exclude=minutely,hourly&appid={os.getenv("OPENWEATHER_API_KEY")}'
        )

        if weather_response.status_code != 200:
            return weather_response.reason

        forecast_data = weather_response.json().get("list", [])

        current_weather = forecast_data[0] if forecast_data else {}
        daily_forecast = [forecast_data[i] for i in range(0, len(forecast_data), 8)]

        forecast_text = generate_forecast_text(daily_forecast)

        return forecast_text

    except Exception as e:
        return str(e)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
