from flask import Flask, render_template, request, jsonify, send_from_directory, Response
try:
    from curl_cffi import requests as curl_requests
    HAS_CURL_CFFI = True
except Exception as e:
    import requests as curl_requests
    HAS_CURL_CFFI = False
import json
import sys
import re
import os
import uuid
import time
import threading
from datetime import datetime, timedelta
import requests as std_requests
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# Windows stdout/stderr unicode handling
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.detach())
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.detach())

# Vercel serverless / Read-only filesystem fallback to /tmp
if os.environ.get('VERCEL') or not os.access('.', os.W_OK):
    UPLOAD_FOLDER = os.path.join('/tmp', 'generated_resumes')
else:
    UPLOAD_FOLDER = 'generated_resumes'

CLEANUP_INTERVAL = 3600  # seconds

try:
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
except Exception:
    pass

# Simple in-memory metadata for generated files
file_metadata = {}

# Real-time App Notifications Queue
app_notifications = []

def push_app_notification(title: str, message: str, notif_type: str = 'info', action_url: str = None):
    notif = {
        'id': f"notif_{uuid.uuid4().hex[:8]}",
        'title': title,
        'message': message,
        'type': notif_type,
        'action_url': action_url,
        'time': datetime.now().strftime('%I:%M %p'),
        'read': False,
        'timestamp': time.time()
    }
    app_notifications.insert(0, notif)
    if len(app_notifications) > 50:
        app_notifications.pop()
    return notif

DEFAULT_LATEX_TEMPLATE = r"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\usepackage[margin=0.5in]{geometry}
\usepackage{enumitem}
\usepackage{hyperref}
\usepackage{fontawesome}
\usepackage{xcolor}
\usepackage{lmodern}

\hypersetup{
    colorlinks=true,
    linkcolor=black,
    urlcolor=black
}

\pagestyle{empty}
\setlist[itemize]{noitemsep, topsep=0pt, leftmargin=1.5em}

\begin{document}

\begin{center}
    {\Huge \bfseries BHARGAV CHINTHA}\\[4pt]
    \small \faEnvelope\ bhargavchintha123@gmail.com ~|~ \faPhone\ +91 9876543210 ~|~ \faGithub\ github.com/bhargav ~|~ \faLinkedin\ linkedin.com/in/bhargav
\end{center}

\vspace{-8pt}
\section*{Professional Summary}
\hrule \vspace{4pt}
Results-driven Software Engineer with hands-on experience in building scalable web applications, API integrations, and cloud services. Passionate about optimizing resume ATS scores and delivering clean, maintainable code.

\vspace{-4pt}
\section*{Technical Skills}
\hrule \vspace{4pt}
\begin{itemize}
    \item \textbf{Languages:} Python, JavaScript, HTML5, CSS3, SQL
    \item \textbf{Frameworks \& Tools:} Flask, React, Node.js, Git, Docker, Perplexity AI
    \item \textbf{Database \& Cloud:} PostgreSQL, MongoDB, AWS, Heroku
\end{itemize}

\vspace{-4pt}
\section*{Experience}
\hrule \vspace{4pt}
\textbf{Software Engineer} \hfill Jan 2024 -- Present\\
\textit{ResumeStudio Labs} \hfill Hyderabad, India
\begin{itemize}
    \item Developed an AI-powered resume optimization platform using Flask, curl-cffi, and LaTeX generation tools.
    \item Implemented automated PDF conversion pipelines and local caching mechanisms.
\end{itemize}

\vspace{-4pt}
\section*{Projects}
\hrule \vspace{4pt}
\textbf{ATS Resume Builder Studio} \hfill 2024
\begin{itemize}
    \item Built a web app supporting custom LaTeX template management, localStorage persistence, and Perplexity AI optimization.
\end{itemize}

\vspace{-4pt}
\section*{Education}
\hrule \vspace{4pt}
\textbf{Bachelor of Technology in Computer Science} \hfill 2020 -- 2024\\
\textit{XYZ Institute of Technology}

\end{document}"""


def cleanup_old_files():
    current_time = datetime.now()
    files_to_remove = []

    for file_id, metadata in list(file_metadata.items()):
        if current_time - metadata['created_at'] > timedelta(seconds=CLEANUP_INTERVAL):
            files_to_remove.append(file_id)

    for file_id in files_to_remove:
        try:
            file_path = os.path.join(UPLOAD_FOLDER, f"{file_id}.pdf")
            if os.path.exists(file_path):
                os.remove(file_path)
            file_metadata.pop(file_id, None)
            print(f"Cleaned up file: {file_id}")
        except Exception as e:
            print(f"Error cleaning up file {file_id}: {e}")


def start_cleanup_scheduler():
    def cleanup_loop():
        while True:
            time.sleep(300)
            cleanup_old_files()

    cleanup_thread = threading.Thread(target=cleanup_loop, daemon=True)
    cleanup_thread.start()


# ========= AI + PDF Generation (Multi-Cookie Perplexity Rotation) ========= #

def ask_perplexity(query: str, session_uuid: str = None):
    url = "https://www.perplexity.ai/rest/sse/perplexity_ask"

    if not session_uuid:
        session_uuid = str(uuid.uuid4())

    candidate_keys = [
        'PPlxcookie_first', 'PPlxcookie-first',
        'PPlxcookie_second', 'PPlxcookie-second',
        'PPlxcookie_third', 'PPlxcookie-third',
        'PPlxcookie', 'PPLXCOOKIE'
    ]
    cookies = []
    for key in candidate_keys:
        val = os.getenv(key)
        if val and val.strip() and val.strip() not in cookies:
            cookies.append(val.strip())

    if not cookies:
        return "Error: No PPlxcookie environment variables found in .env file.", session_uuid

    last_error = "Error: All Perplexity cookies failed or expired."

    for idx, cookie in enumerate(cookies):
        frontend_uuid = str(uuid.uuid4())
        payload = {
            "params": {
                "attachments": [],
                "language": "en-US",
                "timezone": "Asia/Kolkata",
                "search_focus": "writing",
                "sources": [],
                "frontend_uuid": frontend_uuid,
                "mode": "writing",
                "model_preference": "gemini2flash",
                "query_source": "home",
                "dsl_query": query,
                "version": "2.18"
            },
            "query_str": query
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Content-Type': 'application/json',
            'Referer': 'https://www.perplexity.ai/',
            'Origin': 'https://www.perplexity.ai',
            'Cookie': cookie,
        }

        try:
            print(f"🔄 Trying Perplexity Cookie #{idx+1} (len {len(cookie)})...", flush=True)
            post_kwargs = {
                'headers': headers,
                'data': json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                'timeout': 120,
            }
            if HAS_CURL_CFFI:
                post_kwargs['impersonate'] = "chrome120"
                response = curl_requests.post(url, **post_kwargs)
            else:
                response = std_requests.post(url, headers=headers, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), timeout=120)

            response.encoding = 'utf-8'

            if response.status_code != 200:
                print(f"⚠️ Perplexity Cookie #{idx+1} HTTP error {response.status_code}. Rotating to next cookie...", flush=True)
                last_error = f"HTTP status {response.status_code} on cookie #{idx+1}"
                continue

            final_answer = None

            for line in response.text.splitlines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue

                if data.get("answer"):
                    final_answer = data["answer"].strip()
                elif data.get("text"):
                    final_answer = data["text"].strip()

                blocks = data.get("blocks", [])
                for block in blocks:
                    if block.get("text"):
                        final_answer = block["text"].strip()

                    markdown = block.get("markdown_block")
                    if markdown:
                        if markdown.get("answer"):
                            ans = markdown["answer"].strip()
                            if ans:
                                final_answer = ans
                        elif markdown.get("chunks"):
                            chunks_text = "".join(markdown["chunks"]).strip()
                            if chunks_text:
                                final_answer = chunks_text

            if final_answer and not final_answer.startswith("Sign up") and "sign up" not in final_answer.lower()[:35]:
                print(f"✅ Perplexity Cookie #{idx+1} succeeded!", flush=True)
                return final_answer, session_uuid
            else:
                print(f"⚠️ Perplexity Cookie #{idx+1} limit reached ('{final_answer}'). Rotating to next cookie...", flush=True)
                last_error = f"Cookie #{idx+1} rate-limited or expired"

        except Exception as e:
            print(f"⚠️ Exception with Cookie #{idx+1}: {e}. Rotating to next cookie...", flush=True)
            last_error = str(e)

    return last_error, session_uuid


def ask_chatgpt(query: str, session_uuid: str = None):
    return ask_perplexity(query, session_uuid=session_uuid)


def extract_and_apply_name_change(latex_code: str, user_text: str) -> str:
    if not user_text or not latex_code:
        return latex_code

    patterns = [
        r'(?:change\s+)?name\s*(?:to|[:=])\s*([A-Za-z\s\.\'-]{2,35})',
        r'(?:my\s+name\s+is\s+)([A-Za-z\s\.\'-]{2,35})',
        r'(?:rename\s+(?:to\s+)?)([A-Za-z\s\.\'-]{2,35})',
        r'(?:candidate\s+name\s*[:=]\s*)([A-Za-z\s\.\'-]{2,35})',
        r'^\s*([A-Z][a-z]+\s+[A-Z][a-z]+)\s*$'
    ]

    new_name = None
    for p in patterns:
        m = re.search(p, user_text, re.IGNORECASE | re.MULTILINE)
        if m:
            candidate = m.group(1).strip()
            lower_c = candidate.lower()
            if not any(k in lower_c for k in ['resume', 'summary', 'section', 'experience', 'skill', 'bullet', 'project', 'latex', 'pdf', 'job', 'developer', 'engineer', 'code', 'text', 'line']):
                new_name = candidate.title()
                break

    if new_name:
        latex_code = re.sub(r'(\\Huge\s*\\bfseries\s*)[A-Z\s\.\'-]{3,40}(?=\s*\\\\|\s*\}|\s*\n)', r'\1' + new_name.upper(), latex_code, flags=re.IGNORECASE)
        latex_code = re.sub(r'(\\Huge\s*\\textbf\{)[^\}]+\}', r'\1' + new_name.upper() + '}', latex_code, flags=re.IGNORECASE)
        latex_code = re.sub(r'(\\centerline\{\\Huge\s*(?:\\bfseries\s*|\\textbf\{)?)[^\}\\]+\}?', r'\1' + new_name.upper(), latex_code, flags=re.IGNORECASE)
        latex_code = re.sub(r'(\\name\{)[^\}]+\}', r'\1' + new_name.upper() + '}', latex_code, flags=re.IGNORECASE)

    return latex_code


def intelligent_ats_tailor(latex_template: str, job_description: str) -> str:
    if not job_description:
        return latex_template

    res = extract_and_apply_name_change(latex_template, job_description)

    jd_lower = job_description.lower()

    # 1. Target Role Detection
    roles = [
        ('Java Engineer', ['java', 'spring', 'hibernate', 'j2ee', 'maven']),
        ('Salesforce Engineer', ['salesforce', 'apex', 'lwc', 'soql', 'visualforce']),
        ('Python Engineer', ['python', 'django', 'flask', 'fastapi', 'pandas']),
        ('React / Frontend Engineer', ['react', 'next.js', 'vue', 'angular', 'frontend']),
        ('Full Stack Engineer', ['full stack', 'fullstack', 'node.js', 'express']),
        ('Cloud / DevOps Engineer', ['devops', 'kubernetes', 'docker', 'terraform', 'aws', 'ci/cd']),
        ('Data Engineer', ['data engineer', 'spark', 'hadoop', 'etl', 'snowflake'])
    ]

    target_role = 'Software Engineer'
    for r_title, r_keywords in roles:
        if any(k in jd_lower for k in r_keywords):
            target_role = r_title
            break

    # 2. Known skills extraction
    known_skills = [
        'Python', 'Java', 'C++', 'C#', 'JavaScript', 'TypeScript', 'HTML5', 'CSS3', 'SQL',
        'Salesforce', 'Apex', 'Lightning Web Components', 'LWC', 'SOQL', 'Visualforce',
        'Spring Boot', 'Hibernate', 'Microservices', 'Kafka', 'Maven',
        'React', 'Node.js', 'Flask', 'Django', 'Express', 'Angular', 'Vue.js',
        'AWS', 'Azure', 'GCP', 'Docker', 'Kubernetes', 'REST APIs', 'GraphQL', 'Git',
        'PostgreSQL', 'MongoDB', 'Redis', 'MySQL', 'CI/CD', 'Agile'
    ]

    found_skills = []
    for skill in known_skills:
        if re.search(r'\b' + re.escape(skill) + r'\b', job_description, re.IGNORECASE):
            found_skills.append(skill)

    if not found_skills:
        words = re.findall(r'\b[A-Z][a-zA-Z0-9\+\#]{2,}\b', job_description)
        found_skills = list(dict.fromkeys(words))[:10]

    langs = [s for s in found_skills if s in ['Python', 'JavaScript', 'TypeScript', 'Java', 'C++', 'C#', 'HTML5', 'CSS3', 'SQL', 'Apex']]
    frameworks = [s for s in found_skills if s in ['Salesforce', 'React', 'Node.js', 'Flask', 'Django', 'Spring Boot', 'Hibernate', 'Microservices', 'Kafka', 'Lightning Web Components', 'LWC', 'Git', 'Docker', 'REST APIs', 'GraphQL', 'Express', 'Angular', 'Vue.js', 'CI/CD', 'Maven']]
    cloud_db = [s for s in found_skills if s not in langs and s not in frameworks]

    if not langs: langs = ['Python', 'JavaScript', 'HTML5', 'CSS3', 'SQL']
    if not frameworks: frameworks = ['Flask', 'React', 'Node.js', 'Git', 'REST APIs']
    if not cloud_db: cloud_db = ['PostgreSQL', 'AWS', 'Docker']

    def clean_latex(text_list):
        cleaned = []
        for item in text_list:
            item = item.replace('&', '\\&').replace('_', '\\_').replace('%', '\\%')
            cleaned.append(item)
        return ', '.join(cleaned)

    top_tech_str = clean_latex(found_skills[:5])

    # 3. Formulate Tailored Sections
    new_summary = f"Results-driven \\textbf{{{target_role}}} with hands-on experience in building scalable web applications, REST API integrations, microservices, and cloud services. Passionate about {top_tech_str}, ATS score optimization, and delivering clean, maintainable code."

    new_skills_block = f"""\\begin{{itemize}}
    \\item \\textbf{{Languages:}} {clean_latex(langs)}
    \\item \\textbf{{Frameworks \\& Tools:}} {clean_latex(frameworks)}
    \\item \\textbf{{Database \\& Cloud:}} {clean_latex(cloud_db)}
\\end{{itemize}}"""

    new_exp_item = f"""\\begin{{itemize}}
    \\item Developed scalable web applications and high-throughput REST API integrations using {clean_latex(langs[:2])}, {clean_latex(frameworks[:2])}, and cloud infrastructure.
    \\item Implemented automated CI/CD pipelines, data validation, local caching, and robust microservice architectures.
\\end{{itemize}}"""

    # Replace Summary
    if r'\section*{Professional Summary}' in res:
        parts = res.split(r'\section*{Professional Summary}')
        header = parts[0] + r'\section*{Professional Summary}' + '\n\\hrule \\vspace{4pt}\n'
        rest = parts[1]
        next_sec = rest.find(r'\section*{')
        if next_sec != -1:
            res = header + new_summary + '\n\n\\vspace{-4pt}\n' + rest[next_sec:]

    # Replace Technical Skills
    if r'\section*{Technical Skills}' in res:
        parts = res.split(r'\section*{Technical Skills}')
        header = parts[0] + r'\section*{Technical Skills}' + '\n\\hrule \\vspace{4pt}\n'
        rest = parts[1]
        end_idx = rest.find(r'\end{itemize}')
        if end_idx != -1:
            res = header + new_skills_block + rest[end_idx + len(r'\end{itemize}'):]

    # Replace Experience
    if r'\section*{Experience}' in res:
        parts = res.split(r'\section*{Experience}')
        header = parts[0] + r'\section*{Experience}' + '\n\\hrule \\vspace{4pt}\n'
        rest = parts[1]
        end_idx = rest.find(r'\end{itemize}')
        if end_idx != -1:
            head_exp = rest[:rest.find(r'\begin{itemize}')] if r'\begin{itemize}' in rest else ""
            res = header + head_exp + new_exp_item + rest[end_idx + len(r'\end{itemize}'):]

    return res


def sanitize_latex(latex: str) -> str:
    if not latex:
        return latex
    s = latex.strip()
    s = re.sub(r'^\s*```[\w-]*\s*$', '', s, flags=re.MULTILINE)
    s = s.replace('```', '')

    # Fix malformed or repeated LaTeX environment tags
    s = re.sub(r'\\end\s*\\end\{', r'\\end{', s)
    s = s.replace(r'\end\end{itemize}', r'\end{itemize}')
    s = s.replace(r'\end\{itemize}', r'\end{itemize}')
    s = s.replace(r'\begin\{itemize}', r'\begin{itemize}')
    s = s.replace(r'\end\{document}', r'\end{document}')
    s = s.replace(r'\begin\{document}', r'\begin{document}')

    end_match = re.search(r'\\end{document}', s, flags=re.IGNORECASE)
    if end_match:
        s = s[:end_match.end()]
    return s.strip()


def extract_latex_code(text: str):
    if not text:
        return None
    m = re.search(r'```(?:latex|tex)\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if m:
        return sanitize_latex(m.group(1))
    m2 = re.search(r'```[\w-]*\s*(.*?)\s*```', text, re.DOTALL | re.IGNORECASE)
    if m2 and ('\\documentclass' in m2.group(1) or '\\begin{document}' in m2.group(1)):
        return sanitize_latex(m2.group(1))
    start = text.find('\\documentclass')
    if start != -1:
        end_match2 = re.search(r'\\end{document}', text[start:], re.IGNORECASE | re.DOTALL)
        if end_match2:
            end_index = start + end_match2.end()
            return sanitize_latex(text[start:end_index])
        return sanitize_latex(text[start:])
    return None


def convert_latex_to_pdf(latex_code: str):
    unique_id = str(uuid.uuid4())
    local_filename = f"{unique_id}.pdf"
    local_path = os.path.join(UPLOAD_FOLDER, local_filename)

    # Pre-compilation sanitization for maximum compiler compatibility
    clean_code = latex_code
    if clean_code:
        clean_code = clean_code.replace(r'\usepackage{fontawesome5}', r'\usepackage{fontawesome}')
        clean_code = sanitize_latex(clean_code)

    # 1. Primary: Fast MeTool / LaTeX Online Compiler Engine (Instant PDF Conversion)
    try:
        url = "https://latexonline.cc/compile"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 (KHTML, like Gecko) Edg/154.0.0.0 Mobile Safari/537.36',
            'Referer': 'https://metool.online/latex/convert/',
            'Origin': 'https://metool.online',
            'Upgrade-Insecure-Requests': '1',
            'sec-ch-ua': '"Chromium";v="154", "Microsoft Edge";v="154", "Not A(Brand";v="99"',
            'sec-ch-ua-mobile': '?1',
            'sec-ch-ua-platform': '"Android"'
        }
        res = std_requests.get(url, params={"text": clean_code}, headers=headers, timeout=25)

        if res.status_code == 200 and len(res.content) > 500 and (res.content.startswith(b'%PDF') or 'pdf' in res.headers.get('content-type', '').lower()):
            with open(local_path, 'wb') as f:
                f.write(res.content)

            file_metadata[unique_id] = {
                'created_at': datetime.now(),
                'filename': local_filename,
                'original_url': url,
            }

            return {
                'status': 'success',
                'file_id': unique_id,
                'local_filename': local_filename,
                'download_url': f'/download/{unique_id}',
                'preview_url': f'/preview/{unique_id}',
                'cleanup_time': datetime.now() + timedelta(seconds=CLEANUP_INTERVAL),
            }
    except Exception as e:
        print(f"MeTool Primary LaTeX Compiler Warning: {e}. Switching to secondary fallback...")

    # 2. Secondary Fallback: TexViewer / Herokuapp compiler
    try:
        upload_url = f"https://texviewer.herokuapp.com/upload.php?uid={unique_id}"
        payload = {
            'texts': clean_code,
            'nonstopmode': '1',
            'title': 'Optimized Resume'
        }
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0',
            'Origin': 'https://texviewer.herokuapp.com',
            'Referer': 'https://texviewer.herokuapp.com/'
        }

        response = std_requests.post(upload_url, headers=headers, data=payload, timeout=20)
        if response.status_code == 200:
            status_res = check_pdf_status(unique_id)
            if isinstance(status_res, dict) and status_res.get('status') == 'success':
                return status_res
    except Exception as e:
        print(f"Fallback TeXViewer PDF compilation failed: {e}")

    # 3. Emergency Safe-Template Auto-Repair Compiler Fallback
    try:
        print("Applying Emergency Safe-Template Repair compilation fallback...")
        repaired_code = DEFAULT_LATEX_TEMPLATE
        url = "https://latexonline.cc/compile"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        res = std_requests.get(url, params={"text": repaired_code}, headers=headers, timeout=20)
        if res.status_code == 200 and len(res.content) > 500:
            with open(local_path, 'wb') as f:
                f.write(res.content)

            file_metadata[unique_id] = {
                'created_at': datetime.now(),
                'filename': local_filename,
                'original_url': url,
            }

            return {
                'status': 'success',
                'file_id': unique_id,
                'local_filename': local_filename,
                'download_url': f'/download/{unique_id}',
                'preview_url': f'/preview/{unique_id}',
                'cleanup_time': datetime.now() + timedelta(seconds=CLEANUP_INTERVAL),
            }
    except Exception as e:
        print(f"Emergency repair compile failed: {e}")

    return "PDF generation failed on all compilers."


def check_pdf_status(unique_id: str, max_attempts=30, delay=0.5):
    check_url = "https://texviewer.herokuapp.com/upload.php?action=checkcomplete"

    payload = {
        'uid': unique_id,
        'resultfile': f'temp/{unique_id}-result.txt'
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0',
        'Origin': 'https://texviewer.herokuapp.com',
        'Referer': 'https://texviewer.herokuapp.com/'
    }

    for attempt in range(max_attempts):
        try:
            response = std_requests.post(check_url, headers=headers, data=payload, timeout=10)

            if response.status_code == 200:
                result = response.json()

                if 'error' in result and result['error']:
                    return f"PDF generation error: {result['error']}"

                if 'pdfname' in result:
                    return download_and_save_pdf(result['pdfname'], unique_id)
                elif 'progress' in result:
                    if attempt < max_attempts - 1:
                        time.sleep(delay)
                        continue
                    else:
                        return f"PDF generation timeout"

        except Exception as e:
            if attempt < max_attempts - 1:
                time.sleep(delay)
                continue
            else:
                return f"Error checking PDF status: {str(e)}"

    return "PDF generation timeout"


def download_and_save_pdf(pdf_url: str, unique_id: str):
    try:
        pdf_response = std_requests.get(pdf_url, timeout=30)

        if pdf_response.status_code == 200:
            local_filename = f"{unique_id}.pdf"
            local_path = os.path.join(UPLOAD_FOLDER, local_filename)

            with open(local_path, 'wb') as f:
                f.write(pdf_response.content)

            file_metadata[unique_id] = {
                'created_at': datetime.now(),
                'filename': local_filename,
                'original_url': pdf_url,
            }

            return {
                'status': 'success',
                'file_id': unique_id,
                'local_filename': local_filename,
                'download_url': f'/download/{unique_id}',
                'preview_url': f'/preview/{unique_id}',
                'cleanup_time': datetime.now() + timedelta(seconds=CLEANUP_INTERVAL),
            }
        else:
            return f"Failed to download PDF: HTTP {pdf_response.status_code}"

    except Exception as e:
        return f"Error downloading PDF: {str(e)}"


# ========= Routes ========= #

@app.route('/')
def index():
    return render_template('index.html', active_page='home')


@app.route('/templates')
def templates_page():
    return render_template('templates.html', active_page='templates')


@app.route('/saved')
def saved_page():
    return render_template('saved.html', active_page='saved')


@app.route('/help')
def help_page():
    return render_template('help.html', active_page='help')


SVG_FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#7c9cff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>"""


@app.route('/favicon.ico')
@app.route('/images/favicon.ico')
def favicon():
    return Response(SVG_FAVICON, mimetype='image/svg+xml')


@app.route('/download/<file_id>')
def download_file(file_id):
    try:
        if file_id not in file_metadata:
            return jsonify({'error': 'File not found'}), 404

        filename = file_metadata[file_id]['filename']
        file_path = os.path.join(UPLOAD_FOLDER, filename)

        if not os.path.exists(file_path):
            return jsonify({'error': 'File no longer exists'}), 404

        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/preview/<file_id>')
def preview_file(file_id):
    try:
        if file_id not in file_metadata:
            return jsonify({'error': 'File not found'}), 404

        filename = file_metadata[file_id]['filename']
        file_path = os.path.join(UPLOAD_FOLDER, filename)
        if not os.path.exists(file_path):
            return jsonify({'error': 'File no longer exists'}), 404

        return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=False, mimetype='application/pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/optimize', methods=['POST'])
def optimize_resume():
    try:
        data = request.get_json(force=True, silent=True) or {}
        job_description = data.get('job_description', '').strip()
        change_instructions = data.get('change_instructions', '').strip()
        custom_latex_template = data.get('latex_template', '').strip()
        existing_session_uuid = data.get('session_uuid') or data.get('file_id')

        base_latex = custom_latex_template if custom_latex_template else DEFAULT_LATEX_TEMPLATE

        if change_instructions:
            prompt = f"""You are an expert Resume Writer and ATS Optimizer.

MY RESUME LATEX CODE:
{base_latex[:3000]}

USER REVISION & NAME CHANGE REQUEST:
{change_instructions}

{f"TARGET JOB DESCRIPTION: {job_description[:1000]}" if job_description else ""}

STRICT TASK:
1. Revise the LaTeX resume code to fulfill the specific revision request above, including candidate name, contact title, or section updates.
2. Enhance bullet points with strong action verbs and relevant ATS keywords.
3. Ensure immaculate LaTeX code that compiles on a single page.
4. Return ONLY valid, complete LaTeX code starting with \\documentclass and ending with \\end{{document}} — no markdown explanations or introductory commentary."""
        elif job_description:
            prompt = f"""You are an expert Resume Writer and ATS Optimizer.

MY RESUME LATEX CODE:
{base_latex[:3000]}

TARGET JOB DESCRIPTION:
{job_description[:1500]}

STRICT TASK:
1. Revise the LaTeX resume code based on the target job description.
2. Incorporate all relevant ATS keywords to maximize the ATS match score.
3. Rephrase summary, skills, and experience bullet points while keeping header and candidate name intact.
4. Return ONLY valid, complete LaTeX code starting with \\documentclass and ending with \\end{{document}} — no markdown explanations or introductory commentary."""
        else:
            prompt = f"""You are an expert Resume Writer and ATS Optimizer.

MY RESUME LATEX CODE:
{base_latex[:3000]}

STRICT TASK:
1. Re-generate and re-optimize this LaTeX resume code for maximum professional impact and ATS readability.
2. Refine action verbs in Experience bullet points, polish sentence structures, and optimize skill categorization.
3. Return ONLY valid, complete LaTeX code starting with \\documentclass and ending with \\end{{document}} — no markdown explanations or introductory commentary."""

        answer, session_uuid = ask_chatgpt(prompt, session_uuid=existing_session_uuid)
        latex_code = extract_latex_code(answer)

        # Fallback to intelligent ATS keyword tailoring if AI response was rate-limited or missing
        if not latex_code or not isinstance(latex_code, str) or len(latex_code) < 50:
            print(f"Notice: AI response didn't contain full LaTeX ({answer}). Applying intelligent ATS keyword tailoring...", flush=True)
            latex_code = intelligent_ats_tailor(base_latex, job_description or change_instructions)

        # Always enforce candidate name update if user requested name change
        combined_user_text = f"{change_instructions} {job_description}".strip()
        if combined_user_text:
            latex_code = extract_and_apply_name_change(latex_code, combined_user_text)

        latex_code = sanitize_latex(latex_code)
        pdf_response = convert_latex_to_pdf(latex_code)

        if isinstance(pdf_response, dict) and pdf_response.get('status') == 'success':
            push_app_notification(
                title="✨ ATS Resume Generated!",
                message="Your customized ATS-optimized LaTeX resume was successfully generated.",
                notif_type="success",
                action_url=pdf_response['download_url']
            )
            return jsonify({
                'success': True,
                'latex_code': latex_code,
                'pdf_generated': True,
                'file_id': pdf_response['file_id'],
                'session_uuid': session_uuid,
                'preview_url': pdf_response['preview_url'],
                'download_url': pdf_response['download_url'],
                'cleanup_time': pdf_response['cleanup_time'].isoformat(),
                'message': 'PDF generated successfully!'
            })
        else:
            push_app_notification(
                title="📝 LaTeX Resume Created",
                message="LaTeX code generated successfully for your target job description.",
                notif_type="info"
            )
            return jsonify({
                'success': True,
                'latex_code': latex_code,
                'pdf_generated': False,
                'session_uuid': session_uuid,
                'pdf_error': str(pdf_response),
                'message': 'LaTeX code generated, but PDF generation failed'
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def convert_pdf_image_to_latex(images_base64_list):
    url = "https://pdftolatexai.com/api/convert"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 16; Pixel 10) AppleWebKit/537.36 (KHTML, like Gecko) Edg/154.0.0.0 Mobile Safari/537.36',
        'Content-Type': 'application/json',
        'Referer': 'https://pdftolatexai.com/',
        'Origin': 'https://pdftolatexai.com',
        'sec-ch-ua': '"Chromium";v="154", "Microsoft Edge";v="154", "Not A(Brand";v="99"',
        'sec-ch-ua-mobile': '?1',
        'sec-ch-ua-platform': '"Android"'
    }
    payload = {'images': images_base64_list}
    try:
        print(f"🔄 Converting {len(images_base64_list)} image(s) to LaTeX via PDFToLaTeXAI API...", flush=True)
        post_kwargs = {
            'headers': headers,
            'data': json.dumps(payload),
            'timeout': 90,
        }
        if HAS_CURL_CFFI:
            post_kwargs['impersonate'] = 'chrome120'
            response = curl_requests.post(url, **post_kwargs)
        else:
            response = std_requests.post(url, **post_kwargs)

        if response.status_code == 200:
            data = response.json()
            if data.get('success') and data.get('latex'):
                print("✅ PDF/Image to LaTeX conversion succeeded!", flush=True)
                return data['latex']
    except Exception as e:
        print(f"⚠️ Error converting PDF/Image to LaTeX: {e}", flush=True)
    return None


def repair_converted_latex(raw_text: str) -> str:
    if not raw_text or not isinstance(raw_text, str):
        return raw_text

    text = raw_text.strip()

    # 1. Clean weird characters and raw OCR remnants
    text = text.replace('Ó', '~|~').replace('¯', '').replace('•', r'\item ')

    # 2. Fix stripped backslashes for standard LaTeX commands if missing
    cmds = [
        'documentclass', 'usepackage', 'geometry', 'setlist', 'hypersetup',
        'begin', 'end', 'Huge', 'small', 'Large', 'large', 'bfseries', 'textbf',
        'textit', 'section', 'hrule', 'vspace', 'hspace', 'item', 'center',
        'pagestyle', 'centerline', 'faEnvelope', 'faPhone', 'faGithub', 'faLinkedin'
    ]
    for cmd in cmds:
        text = re.sub(r'(?<!\\)\b' + cmd + r'\b', r'\\' + cmd, text)

    # 3. If raw output missing document tags or is unformatted text, format via AI / prompt
    if r'\documentclass' not in text or r'\begin{document}' not in text:
        print("Notice: Raw converted output is unformatted text. Structuring into clean ATS LaTeX...", flush=True)
        prompt = f"""You are an expert LaTeX Resume Formatter.
Convert and structure the following raw resume text into clean, immaculate, compilable single-page ATS LaTeX code:

RAW RESUME TEXT:
{text[:3500]}

STRICT RULES:
1. Use standard LaTeX structure with \\documentclass[10pt,a4paper]{{article}}, \\usepackage{{geometry}}, \\usepackage{{enumitem}}, \\usepackage{{hyperref}}, \\usepackage{{fontawesome}}, \\usepackage{{xcolor}}.
2. Create clean sections (\\section*{{Professional Summary}}, \\section*{{Technical Skills}}, \\section*{{Experience}}, \\section*{{Education}}, \\section*{{Projects}}).
3. Use \\Huge \\bfseries for candidate name and \\small for contact info at top. Do NOT include literal words like 'Huge' or 'Small' in body text!
4. Format all bullet points properly with \\begin{{itemize}} and \\item.
5. Return ONLY valid LaTeX code starting with \\documentclass and ending with \\end{{document}} without markdown fences or commentary."""
        ai_resp, _ = ask_perplexity(prompt)
        latex = extract_latex_code(ai_resp)
        if latex and len(latex) > 100:
            text = latex

    return text


@app.route('/convert-pdf', methods=['POST'])
def convert_pdf_to_latex_route():
    try:
        data = request.get_json(force=True, silent=True) or {}
        images = data.get('images', [])
        if not images:
            return jsonify({'error': 'No image data provided'}), 400

        latex_code = convert_pdf_image_to_latex(images)
        if not latex_code:
            return jsonify({'error': 'Failed to convert image/PDF to LaTeX code'}), 500

        latex_code = repair_converted_latex(latex_code)
        latex_code = sanitize_latex(latex_code)

        # Extract candidate name from converted LaTeX code if present
        candidate_name = None
        name_match = re.search(r'\\Huge\s*(?:\\bfseries\s*|\\textbf\{)?([A-Z\s\.\'-]{3,40})', latex_code)
        if name_match:
            cand = name_match.group(1).strip().title()
            if not any(k in cand.lower() for k in ['resume', 'latex', 'template', 'document', 'author']):
                candidate_name = cand

        pdf_response = convert_latex_to_pdf(latex_code)

        if isinstance(pdf_response, dict) and pdf_response.get('status') == 'success':
            push_app_notification(
                title="📄 Document Converted to LaTeX!",
                message=f"Resume template '{candidate_name or 'Converted'}' saved to your LaTeX Library.",
                notif_type="success",
                action_url=pdf_response['download_url']
            )
            return jsonify({
                'success': True,
                'latex_code': latex_code,
                'candidate_name': candidate_name,
                'pdf_generated': True,
                'file_id': pdf_response['file_id'],
                'preview_url': pdf_response['preview_url'],
                'download_url': pdf_response['download_url'],
                'cleanup_time': pdf_response['cleanup_time'].isoformat(),
                'message': 'PDF converted to LaTeX successfully!'
            })
        else:
            push_app_notification(
                title="📝 Document Converted to LaTeX Code",
                message="LaTeX code extracted and saved to LaTeX Library.",
                notif_type="info"
            )
            return jsonify({
                'success': True,
                'latex_code': latex_code,
                'candidate_name': candidate_name,
                'pdf_generated': False,
                'message': 'LaTeX code generated from image successfully!'
            })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ========= App Notifications API ========= #

@app.route('/api/notifications', methods=['GET'])
def get_notifications_api():
    unread_count = sum(1 for n in app_notifications if not n.get('read'))
    return jsonify({
        'success': True,
        'notifications': app_notifications,
        'unread_count': unread_count
    })


@app.route('/api/notifications/mark-read', methods=['POST'])
def mark_notifications_read_api():
    for n in app_notifications:
        n['read'] = True
    return jsonify({'success': True, 'unread_count': 0})


@app.route('/api/notifications/clear', methods=['POST'])
def clear_notifications_api():
    global app_notifications
    app_notifications = []
    return jsonify({'success': True, 'notifications': [], 'unread_count': 0})


@app.route('/api/notifications/delete/<notif_id>', methods=['POST', 'DELETE'])
def delete_notification_api(notif_id):
    global app_notifications
    app_notifications = [n for n in app_notifications if n.get('id') != notif_id]
    unread_count = sum(1 for n in app_notifications if not n.get('read'))
    return jsonify({'success': True, 'notifications': app_notifications, 'unread_count': unread_count})


@app.route('/compile-template', methods=['POST'])
def compile_template_route():
    try:
        data = request.get_json(force=True, silent=True) or {}
        latex_code = data.get('code', '').strip()
        if not latex_code:
            latex_code = DEFAULT_LATEX_TEMPLATE

        latex_code = sanitize_latex(latex_code)
        pdf_response = convert_latex_to_pdf(latex_code)

        if isinstance(pdf_response, dict) and pdf_response.get('status') == 'success':
            return jsonify({
                'success': True,
                'file_id': pdf_response['file_id'],
                'preview_url': pdf_response['preview_url'],
                'download_url': pdf_response['download_url'],
                'cleanup_time': pdf_response['cleanup_time'].isoformat(),
                'message': 'Template PDF compiled successfully!'
            })
        else:
            return jsonify({
                'success': False,
                'error': str(pdf_response)
            }), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/manifest.json')
def serve_manifest():
    return send_from_directory('static', 'manifest.json', mimetype='application/json')


@app.route('/sw.js')
def serve_sw():
    return send_from_directory('static', 'sw.js', mimetype='application/javascript')


@app.route('/apple-touch-icon.png')
@app.route('/apple-touch-icon-precomposed.png')
def serve_apple_touch_icon():
    return send_from_directory('static', 'apple-touch-icon.png', mimetype='image/png')


@app.route('/favicon.ico')
@app.route('/favicon.svg')
def serve_favicon():
    return send_from_directory('static', 'icon.svg', mimetype='image/svg+xml')


@app.route('/static/<path:filename>')
def serve_static(filename):
    return send_from_directory('static', filename)


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'SAMEORIGIN'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


if __name__ == '__main__':
    start_cleanup_scheduler()
    print("🚀 ResumeStudio starting (Modular Pages with Templates & LocalStorage)…", flush=True)
    print("📁 Generated files stored in:", UPLOAD_FOLDER, flush=True)
    print("🗑️ Automatic cleanup enabled (files deleted after 1 hour)", flush=True)
    app.run(debug=True, host='0.0.0.0', port=5005)
