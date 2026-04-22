from fastapi import FastAPI
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
import json
import re
from playwright.async_api import async_playwright

app = FastAPI()

class URLRequest(BaseModel):
    url: str


@app.get("/")
def root():
    return {"message": "Extraction service running"}


# 🔹 Clean HTML
def clean_html(text):
    if not text:
        return ""

    soup = BeautifulSoup(text, "html.parser")
    clean_text = soup.get_text(separator=" ")

    clean_text = re.sub(r"[^\x00-\x7F]+", " ", clean_text)
    clean_text = re.sub(r"\s+", " ", clean_text).strip()

    return clean_text


# 🔹 Sections
def extract_sections(text):
    sections = {"responsibilities": "", "qualifications": ""}
    text_lower = text.lower()

    if "responsibilities" in text_lower:
        parts = re.split(r"responsibilities", text, flags=re.IGNORECASE)
        if len(parts) > 1:
            sections["responsibilities"] = parts[1].strip()

    if "qualification" in text_lower or "requirements" in text_lower:
        parts = re.split(r"qualification|requirements", text, flags=re.IGNORECASE)
        if len(parts) > 1:
            sections["qualifications"] = parts[1].strip()

    return sections


# 🔥 NEW: Extra field extraction
def extract_extra_fields(text):
    text_lower = text.lower()

    # Education
    education = ""
    edu_match = re.search(r"(b\.?tech|bachelor|master|m\.?tech|degree)", text_lower)
    if edu_match:
        education = edu_match.group()

    # Experience
    experience = ""
    exp_match = re.search(r"(\d+\+?\s*(years|yrs))", text_lower)
    if exp_match:
        experience = exp_match.group()

    # Skills
    skills = []
    skill_keywords = [
        "python","java","react","node","sql","aws","c++",
        "javascript","docker","kubernetes","html","css"
    ]
    for skill in skill_keywords:
        if skill in text_lower:
            skills.append(skill.capitalize())

    # Salary
    salary = ""
    sal_match = re.search(r"(₹?\s?\d+\s?(lpa|lakhs|per annum))", text_lower)
    if sal_match:
        salary = sal_match.group()

    # Perks
    perks = []
    perk_keywords = ["wfh","remote","health insurance","bonus","flexible"]
    for perk in perk_keywords:
        if perk in text_lower:
            perks.append(perk.capitalize())

    # Department
    department = ""
    if "engineering" in text_lower:
        department = "Engineering"
    elif "finance" in text_lower:
        department = "Finance"
    elif "marketing" in text_lower:
        department = "Marketing"

    return {
        "education": education,
        "experienceLevel": experience,
        "skills": skills,
        "salary": salary,
        "perks": perks,
        "department": department
    }


# 🔹 JSON-LD extraction
def extract_json_ld(html):
    soup = BeautifulSoup(html, "html.parser")
    scripts = soup.find_all("script", type="application/ld+json")

    for script in scripts:
        try:
            if not script.string:
                continue

            data = json.loads(script.string)

            if isinstance(data, list):
                for item in data:
                    if item.get("@type") == "JobPosting":
                        return item
            elif isinstance(data, dict):
                if data.get("@type") == "JobPosting":
                    return data

        except Exception:
            continue

    return None


# 🔹 Playwright fetch
async def fetch_with_playwright(url):
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            await page.goto(url, timeout=30000)
            await page.wait_for_timeout(2000)

            content = await page.content()
            text = await page.inner_text("body")

            await browser.close()

            return content, text

    except Exception as e:
        print("Playwright error:", e)
        return None, None


# 🔹 Heuristic fallback
def extract_from_text(text):
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    job_title = next((l for l in lines[:10] if len(l) > 5), "")
    location = next(
        (l for l in lines if any(x in l.lower() for x in ["india","remote","bangalore","hyderabad"])),
        ""
    )

    return {
        "jobTitle": job_title,
        "location": location,
        "description": text[:2000]
    }


@app.post("/extract-job-using-link")
async def extract_job(data: URLRequest):
    try:
        url = data.url
        if not url:
            return {"success": False, "message": "URL is required"}

        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept-Language": "en-US,en;q=0.9"
        }

        # STEP 1: Requests
        try:
            response = requests.get(url, headers=headers, timeout=10)
        except Exception as e:
            return {"success": False, "message": str(e)}

        if response.status_code == 200:
            html = response.text
            job_data = extract_json_ld(html)

            if job_data:
                clean_desc = clean_html(job_data.get("description"))
                sections = extract_sections(clean_desc)
                extra = extract_extra_fields(clean_desc)

                return {
                    "success": True,
                    "source": "json-ld",
                    "data": {
                        "jobTitle": job_data.get("title"),
                        "companyName": job_data.get("hiringOrganization", {}).get("name"),
                        "location": job_data.get("jobLocation", [{}])[0]
                                    .get("address", {}).get("addressLocality"),

                        "description": clean_desc,
                        "responsibilities": sections["responsibilities"],
                        "qualifications": sections["qualifications"],

                        "education": extra["education"],
                        "experienceLevel": extra["experienceLevel"],
                        "skills": extra["skills"],
                        "salary": job_data.get("baseSalary", {}).get("value", {}).get("value") or extra["salary"],
                        "perks": extra["perks"],
                        "department": extra["department"],
                        "expiryDate": job_data.get("validThrough")
                    }
                }

        # STEP 2: Playwright fallback
        html, text = await fetch_with_playwright(url)

        if html:
            job_data = extract_json_ld(html)

            if job_data:
                clean_desc = clean_html(job_data.get("description"))
                sections = extract_sections(clean_desc)
                extra = extract_extra_fields(clean_desc)

                return {
                    "success": True,
                    "source": "playwright-jsonld",
                    "data": {
                        "jobTitle": job_data.get("title"),
                        "companyName": job_data.get("hiringOrganization", {}).get("name"),
                        "location": job_data.get("jobLocation", [{}])[0]
                                    .get("address", {}).get("addressLocality"),

                        "description": clean_desc,
                        "responsibilities": sections["responsibilities"],
                        "qualifications": sections["qualifications"],

                        "education": extra["education"],
                        "experienceLevel": extra["experienceLevel"],
                        "skills": extra["skills"],
                        "salary": job_data.get("baseSalary", {}).get("value", {}).get("value") or extra["salary"],
                        "perks": extra["perks"],
                        "department": extra["department"],
                        "expiryDate": job_data.get("validThrough")
                    }
                }

            # fallback text parsing
            parsed = extract_from_text(text)
            extra = extract_extra_fields(text)

            parsed.update({
                "education": extra["education"],
                "experienceLevel": extra["experienceLevel"],
                "skills": extra["skills"],
                "salary": extra["salary"],
                "perks": extra["perks"],
                "department": extra["department"],
                "expiryDate": ""
            })

            return {
                "success": True,
                "source": "playwright-text",
                "data": parsed
            }

        return {"success": False, "message": "Extraction failed"}

    except Exception as e:
        return {"success": False, "message": str(e)}