import os
import base64
import time
import threading
import logging

from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
from github import Github, GithubException
import requests

# --- Configuration and Initialization ---
# Load environment variables from .env file
load_dotenv()

# Set up basic logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Initialize Flask App
app = Flask(__name__)

# Get secrets and configurations from environment variables
MY_SECRET = os.getenv("MY_SECRET")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
# Use AIPIPE_TOKEN for authentication
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN")
# Define the AI Pipe base URL and the model
AIPIPE_BASE_URL = "https://aipipe.org/openrouter/v1"
# Using Gemini via OpenRouter as an example
MODEL_NAME = "google/gemini-flash-1.5"

# Check for missing essential configurations
if not all([MY_SECRET, GITHUB_TOKEN, AIPIPE_TOKEN]):
    raise ValueError(
        "One or more environment variables are missing (MY_SECRET, GITHUB_TOKEN, AIPIPE_TOKEN)")

# Initialize API clients
try:
    g = Github(GITHUB_TOKEN)
    github_user = g.get_user()
    # Initialize OpenAI client to point to AI Pipe proxy
    openai_client = OpenAI(
        api_key=AIPIPE_TOKEN,
        base_url=AIPIPE_BASE_URL,
    )
    logging.info(
        f"Successfully authenticated with GitHub as '{github_user.login}'")
    logging.info(f"AI Pipe client configured for model: {MODEL_NAME}")
except Exception as e:
    logging.error(f"Failed to initialize API clients: {e}")
    raise

# --- Helper Functions (No changes needed in the functions below) ---


def get_mit_license():
    """Returns the text for the MIT License."""
    return f"""MIT License

Copyright (c) {time.strftime('%Y')} {github_user.name or github_user.login}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""


def generate_code_from_brief(brief, attachments):
    """Generates code using an LLM based on a brief and attachments."""
    logging.info("Generating code from brief...")
    attachment_content = ""
    if attachments:
        for attachment in attachments:
            header, encoded = attachment['url'].split(",", 1)
            decoded_data = base64.b64decode(encoded).decode('utf-8')
            attachment_content += f"\n\n--- Attachment: {attachment['name']} ---\n{decoded_data}"

    prompt = f"""
    You are an expert web developer. Your task is to generate a single, self-contained `index.html` file based on the provided brief and attachments.
    **Instructions:**
    1.  All HTML, CSS, and JavaScript must be in a single `index.html` file.
    2.  Use `<style>` tags for CSS and `<script>` tags for JavaScript.
    3.  Your response must contain ONLY the raw HTML code and nothing else. Do not wrap it in markdown backticks or add any explanations.
    **Brief:**
    {brief}
    {attachment_content}
    """

    try:
        response = openai_client.chat.completions.create(
            model=MODEL_NAME,  # Use the configured model
            messages=[{"role": "user", "content": prompt}]
        )
        code = response.choices[0].message.content.strip()
        logging.info("Code generation successful.")
        return code
    except Exception as e:
        logging.error(f"Error during AI Pipe API call: {e}")
        return None


def generate_readme(brief):
    """Generates a README.md file content using an LLM."""
    logging.info("Generating README.md...")
    prompt = f"""
    You are a technical writer. Create a professional `README.md` file for a GitHub project.
    The project is a simple web application with the following brief: "{brief}"
    The README should include: a title, summary, setup, usage, code explanation, and license section.
    """
    try:
        response = openai_client.chat.completions.create(
            model=MODEL_NAME,  # Use the configured model
            messages=[{"role": "user", "content": prompt}]
        )
        readme_content = response.choices[0].message.content.strip()
        logging.info("README.md generation successful.")
        return readme_content
    except Exception as e:
        logging.error(f"Error generating README: {e}")
        return "Project README"  # Fallback

# ... (The rest of the file: enable_github_pages, notify_evaluator, process_build_request, process_revise_request, etc. remains exactly the same) ...


def enable_github_pages(repo):
    """Enables GitHub Pages for a repository and returns the URL."""
    logging.info(f"Enabling GitHub Pages for repo: {repo.full_name}")
    try:
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        }
        source = {"branch": repo.default_branch, "path": "/"}
        url = f"https://api.github.com/repos/{repo.full_name}/pages"
        response = requests.post(url, headers=headers, json={"source": source})

        if response.status_code == 409:  # Conflict, pages already exist
            logging.warning("GitHub Pages might already be enabled.")
        else:
            response.raise_for_status()

        pages_url = None
        for _ in range(10):  # Try for up to 50 seconds
            time.sleep(5)
            pages_info = repo.get_pages()
            if pages_info.html_url:
                pages_url = pages_info.html_url.rstrip('/') + '/'
                logging.info(f"GitHub Pages is live at: {pages_url}")
                return pages_url

        logging.error("GitHub Pages URL not available after waiting.")
        return None

    except Exception as e:
        logging.error(f"An error occurred while enabling GitHub Pages: {e}")
        return None


def notify_evaluator(url, payload):
    """Notifies the evaluation URL with exponential backoff."""
    logging.info(f"Notifying evaluation server at {url}...")
    delay = 1
    max_retries = 5
    for i in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers={
                                     'Content-Type': 'application/json'})
            if response.status_code == 200:
                logging.info("Successfully notified evaluation server.")
                return True
            else:
                logging.warning(
                    f"Notification attempt {i+1} failed with status {response.status_code}. Retrying in {delay}s...")
        except requests.exceptions.RequestException as e:
            logging.error(
                f"Notification attempt {i+1} failed with error: {e}. Retrying in {delay}s...")

        time.sleep(delay)
        delay *= 2  # Exponential backoff

    logging.error("Failed to notify evaluation server after all retries.")
    return False


def process_build_request(data):
    """Handles the entire Round 1: Build process."""
    repo_name = data['task']
    code = generate_code_from_brief(data['brief'], data.get('attachments'))
    readme_content = generate_readme(data['brief'])
    if not code or not readme_content:
        logging.error("Failed to generate required content. Aborting.")
        return

    try:
        logging.info(f"Creating new public repository: {repo_name}")
        repo = github_user.create_repo(repo_name, private=False)
        repo.create_file("LICENSE", "feat: Add MIT License", get_mit_license())
        repo.create_file("README.md", "feat: Add README", readme_content)
        commit_info = repo.create_file(
            "index.html", "feat: Initial application code", code)
        commit_sha = commit_info['commit'].sha
        logging.info(f"Pushed initial files. Commit SHA: {commit_sha}")
        pages_url = enable_github_pages(repo)
        if not pages_url:
            logging.error(
                "Could not activate GitHub Pages. Aborting notification.")
            return
        payload = {
            "email": data['email'], "task": data['task'], "round": 1, "nonce": data['nonce'],
            "repo_url": repo.html_url, "commit_sha": commit_sha, "pages_url": pages_url,
        }
        notify_evaluator(data['evaluation_url'], payload)
    except GithubException as e:
        if e.status == 422 and "name already exists" in str(e.data):
            logging.warning(
                f"Repo '{repo_name}' already exists. Aborting build process.")
        else:
            logging.error(
                f"An error occurred with the GitHub API during build: {e}")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during build process: {e}")


def process_revise_request(data):
    """Handles the entire Round 2: Revise process."""
    repo_name = data['task']
    try:
        logging.info(f"Revising existing repository: {repo_name}")
        repo = g.get_repo(f"{github_user.login}/{repo_name}")
        contents = repo.get_contents("index.html")
        existing_code = contents.decoded_content.decode('utf-8')

        logging.info("Generating revised code...")
        prompt = f"""
        You are an expert web developer. You need to update an existing `index.html` file.
        **New Brief for Revision:**
        {data['brief']}
        ---
        **Existing `index.html` Code:**
        ```html
        {existing_code}
        ```
        ---
        **Instructions:**
        1. Modify the existing code to incorporate the new features from the brief.
        2. Your response must contain ONLY the complete, updated HTML code.
        """
        response = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "user", "content": prompt}]
        )
        new_code = response.choices[0].message.content.strip()
        commit_info = repo.update_file(
            contents.path, "feat: Revise application code for round 2", new_code, contents.sha)
        commit_sha = commit_info['commit'].sha
        logging.info(f"Updated index.html. Commit SHA: {commit_sha}")

        pages_url = repo.get_pages().html_url.rstrip('/') + '/'
        payload = {
            "email": data['email'], "task": data['task'], "round": 2, "nonce": data['nonce'],
            "repo_url": repo.html_url, "commit_sha": commit_sha, "pages_url": pages_url,
        }
        notify_evaluator(data['evaluation_url'], payload)
    except GithubException as e:
        logging.error(f"GitHub API error during revise: {e}")
    except Exception as e:
        logging.error(
            f"An unexpected error occurred during revise process: {e}")


def process_request(data):
    """Wrapper function to decide whether to build or revise."""
    round_number = data.get('round')
    logging.info(
        f"Processing request for task '{data.get('task')}', round {round_number}")
    if round_number == 1:
        process_build_request(data)
    elif round_number == 2:
        process_revise_request(data)
    else:
        logging.error(f"Invalid round number: {round_number}")
    logging.info(
        f"Finished processing request for task '{data.get('task')}', round {round_number}")


@app.route('/api-endpoint', methods=['POST'])
def handle_request():
    """Main webhook to receive requests from the evaluation system."""
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    data = request.get_json()
    if data.get('secret') != MY_SECRET:
        logging.warning("Received a request with an invalid secret.")
        return jsonify({"error": "Invalid secret"}), 403
    thread = threading.Thread(target=process_request, args=(data,))
    thread.daemon = True
    thread.start()
    logging.info(
        f"Request for task '{data.get('task')}' received and is being processed in the background.")
    return jsonify({"status": "Request received successfully. Processing in background."}), 200


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001)
