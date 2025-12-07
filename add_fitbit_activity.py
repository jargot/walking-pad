#!/usr/bin/env python3

import os
import sys
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

class FitbitActivityLogger:
    def __init__(self):
        self.access_token = os.getenv('FITBIT_ACCESS_TOKEN')
        self.base_url = 'https://api.fitbit.com/1'

        if not self.access_token:
            raise ValueError("Missing FITBIT_ACCESS_TOKEN in .env file")

    def add_walking_activity(self, steps, start_time, duration_minutes, date=None):
        """
        Add a walking activity with steps to Fitbit.

        Args:
            steps: Number of steps taken
            start_time: Start time in format "HH:MM" (e.g., "11:42")
            duration_minutes: Duration in minutes
            date: Date in YYYY-MM-DD format (defaults to today)
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        # Walking activity ID from Fitbit catalog
        activity_id = 90013  # Walking
        duration_millis = duration_minutes * 60 * 1000  # Convert to milliseconds

        url = f"{self.base_url}/user/-/activities.json"

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'activityId': activity_id,
            'startTime': start_time,
            'durationMillis': duration_millis,
            'date': date,
            'distance': steps,  # Using steps as distance
            'distanceUnit': 'steps'
        }

        try:
            response = requests.post(url, headers=headers, data=data)

            if response.status_code == 401:
                print("❌ Token expired or invalid. Run setup_fitbit_oauth.py to get a new token.")
                return None

            response.raise_for_status()

            result = response.json()
            print("✅ Activity logged successfully!")

            activity_log = result.get('activityLog', {})
            print(f"📊 Activity Details:")
            print(f"   Name: {activity_log.get('name', 'Unknown')}")
            print(f"   Steps: {activity_log.get('steps', 0)}")
            print(f"   Duration: {activity_log.get('duration', 0) // 60000} minutes")
            print(f"   Start Time: {activity_log.get('startTime', 'Unknown')}")
            print(f"   Date: {activity_log.get('startDate', 'Unknown')}")
            print(f"   Calories: {activity_log.get('calories', 0)}")
            print(f"   Log ID: {activity_log.get('logId', 'Unknown')}")

            return result

        except requests.exceptions.RequestException as e:
            print(f"❌ Error logging activity: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return None

    def add_custom_activity(self, activity_name, calories, start_time, duration_minutes, date=None):
        """
        Add a custom activity to Fitbit.

        Args:
            activity_name: Name of the custom activity
            calories: Manual calories burned
            start_time: Start time in format "HH:MM"
            duration_minutes: Duration in minutes
            date: Date in YYYY-MM-DD format (defaults to today)
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')

        duration_millis = duration_minutes * 60 * 1000

        url = f"{self.base_url}/user/-/activities.json"

        headers = {
            'Authorization': f'Bearer {self.access_token}',
            'Content-Type': 'application/x-www-form-urlencoded'
        }

        data = {
            'activityName': activity_name,
            'manualCalories': calories,
            'startTime': start_time,
            'durationMillis': duration_millis,
            'date': date
        }

        try:
            response = requests.post(url, headers=headers, data=data)

            if response.status_code == 401:
                print("❌ Token expired or invalid. Run setup_fitbit_oauth.py to get a new token.")
                return None

            response.raise_for_status()

            result = response.json()
            print("✅ Custom activity logged successfully!")

            activity_log = result.get('activityLog', {})
            print(f"📊 Activity Details:")
            print(f"   Name: {activity_log.get('name', 'Unknown')}")
            print(f"   Duration: {activity_log.get('duration', 0) // 60000} minutes")
            print(f"   Start Time: {activity_log.get('startTime', 'Unknown')}")
            print(f"   Date: {activity_log.get('startDate', 'Unknown')}")
            print(f"   Calories: {activity_log.get('calories', 0)}")
            print(f"   Log ID: {activity_log.get('logId', 'Unknown')}")

            return result

        except requests.exceptions.RequestException as e:
            print(f"❌ Error logging custom activity: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"Response: {e.response.text}")
            return None

def main():
    try:
        logger = FitbitActivityLogger()

        if len(sys.argv) < 2:
            print("🚶 Fitbit Activity Logger\n")
            print("Usage:")
            print("  Add walking activity:")
            print("    python add_fitbit_activity.py walking <steps> <start_time> <duration_minutes> [date]")
            print("    Example: python add_fitbit_activity.py walking 169 11:42 3")
            print("    Example: python add_fitbit_activity.py walking 169 11:42 3 2023-12-07")
            print()
            print("  Add custom activity:")
            print("    python add_fitbit_activity.py custom <name> <calories> <start_time> <duration_minutes> [date]")
            print("    Example: python add_fitbit_activity.py custom \"Treadmill Walking\" 50 11:42 3")
            print()
            print("Times should be in HH:MM format (24-hour)")
            print("Dates should be in YYYY-MM-DD format")
            return

        activity_type = sys.argv[1].lower()

        if activity_type == 'walking':
            if len(sys.argv) < 5:
                print("❌ Usage: python add_fitbit_activity.py walking <steps> <start_time> <duration_minutes> [date]")
                return

            steps = int(sys.argv[2])
            start_time = sys.argv[3]
            duration_minutes = int(sys.argv[4])
            date = sys.argv[5] if len(sys.argv) > 5 else None

            print(f"📝 Logging {steps} steps from {start_time} for {duration_minutes} minutes...")
            logger.add_walking_activity(steps, start_time, duration_minutes, date)

        elif activity_type == 'custom':
            if len(sys.argv) < 6:
                print("❌ Usage: python add_fitbit_activity.py custom <name> <calories> <start_time> <duration_minutes> [date]")
                return

            activity_name = sys.argv[2]
            calories = int(sys.argv[3])
            start_time = sys.argv[4]
            duration_minutes = int(sys.argv[5])
            date = sys.argv[6] if len(sys.argv) > 6 else None

            print(f"📝 Logging custom activity '{activity_name}' ({calories} calories) from {start_time} for {duration_minutes} minutes...")
            logger.add_custom_activity(activity_name, calories, start_time, duration_minutes, date)

        else:
            print("❌ Invalid activity type. Use 'walking' or 'custom'")

    except ValueError as e:
        print(f"❌ Error: {e}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")

if __name__ == "__main__":
    main()