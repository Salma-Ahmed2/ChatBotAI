import json
import os
import requests
import uuid
import datetime
import secrets  # توليد رموز عشوائية آمنة
from typing import Optional, Dict, Any  # added to fix Pylance type error
import typing  # ...existing imports...

USER_DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "user_data.json")
HOURLYLEAD_API = "https://erp.rnr.sa:8005/ar/api/Lead/CreateHourly"
HOUSING_API = "https://erp.rnr.sa:8005/ar/api/ContactAddress/HousingTypes"
NATIONALITY_API = "https://erp.rnr.sa:8005/ar/api/Nationality/ActiveNationalities"

# New: build ADD_ADDRESS_API dynamically from fixedPackage.json
FIXED_PACKAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "fixedPackage.json")

def _load_fixed_package():
    try:
        with open(FIXED_PACKAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

_fp = _load_fixed_package()
_service_id = _fp.get("service_id") or _fp.get("serviceId") 
_step_id = _fp.get("stepId") or _fp.get("step_id") 

def load_user_data():
    """تحميل بيانات المستخدم من الملف"""
    if os.path.exists(USER_DATA_PATH):
        with open(USER_DATA_PATH, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_user_data(data):
    """حفظ بيانات المستخدم في ملف JSON"""
    with open(USER_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def collect_user_info():
    """
    تتحقق من وجود بيانات المستخدم (الاسم، رقم الهاتف، الايميل، رقم الهوية، النوع، المدينة، الحي)
    ولو ناقصة ترجع النص المناسب لتسأل المستخدم عنها.
    """
    user_data = load_user_data()
    # ترتيب الحقول هنا يحدد ترتيب الأسئلة على المستخدم
    required_fields = {
        "name": "من فضلك أدخل اسمك الكامل:",
        "phone": "من فضلك أدخل رقم هاتفك:",
        "email": "من فضلك أدخل بريدك الإلكتروني (email):",
        # <-- added nationality before national_id
        "nationality": "من فضلك أدخل جنسيتك:",
        "national_id": "من فضلك أدخل رقم الهوية/الإقامة:",
        "gender": "من فضلك اختر النوع: ذكر أو أنثى",
        "city": "من فضلك أدخل اسم مدينتك:",
        "district": "من فضلك أدخل اسم الحي:"
    }

    missing_fields = [key for key in required_fields if not user_data.get(key)]

    if missing_fields:
        next_field = missing_fields[0]
        return required_fields[next_field], next_field  # الرسالة + المفتاح المطلوب
    else:
        return None, None  # كل البيانات متوفرة

def update_user_info(field, value):
    """تحديث حقل معين في بيانات المستخدم"""
    user_data = load_user_data()
    # Normalize and save
    v = value.strip()
    # Arabic-indic digits to western for numeric fields
    trans = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

    if field == "phone":
        v = v.translate(trans)
    elif field == "national_id":
        # normalize digits and remove spaces/dashes
        v = v.translate(trans).replace(" ", "").replace("-", "")
    elif field == "email":
        v = v.lower()
    elif field == "gender":
        # normalize common inputs to Arabic canonical forms
        tmp = v.strip().lower()
        if tmp in ("m", "male", "ذكر", "ذ", "male "):
            v = "ذكر"
        elif tmp in ("f", "female", "أنثى", "انثى", "أنثى", "انثى "):
            v = "أنثى"
        else:
            # keep user input if unrecognized, but trim
            v = v

    # <-- handle nationality field (store multiple compatible keys)
    if field == "nationality":
        # keep the original text (could be country name or code)
        user_data["nationality"] = v

        # Try to fetch list of active nationalities and match by value
        try:
            resp = requests.get(NATIONALITY_API, timeout=10)
            if resp.status_code == 200:
                j = resp.json() or {}
                items = j.get("data") or []
                # exact match first, then case-insensitive
                matched = next((n for n in items if str(n.get("value", "")).strip() == v), None)
                if not matched:
                    matched = next((n for n in items if str(n.get("value", "")).strip().lower() == v.lower()), None)
                if matched:
                    key = matched.get("key")
                    # save compatible keys used elsewhere
                    user_data["nationality_key"] = key
                    user_data["nationalityId"] = key
                    user_data["nationality_id"] = key
                else:
                    # fallback: store the raw text as id-equivalent so downstream logic still sees something
                    user_data.setdefault("nationalityId", v)
                    user_data.setdefault("nationality_id", v)
            else:
                # API failed; store raw text
                user_data.setdefault("nationalityId", v)
                user_data.setdefault("nationality_id", v)
        except Exception:
            # network/error: keep text only
            user_data.setdefault("nationalityId", v)
            user_data.setdefault("nationality_id", v)

        # Update CompleteProfile snapshot body.nationalityId when possible
        try:
            # prepare payload and inject nationalityId (prefer saved key)
            url, body = prepare_complete_profile_payload()
            body["nationalityId"] = user_data.get("nationality_key") or user_data.get("nationalityId") or user_data.get("nationality_id") or v
            # save snapshot (response/headers/status_code left None for now)
            save_complete_profile_snapshot(url=url, body=body, response=None, headers=None, status_code=None)
        except Exception:
            pass

        # persist and return early
        save_user_data(user_data)
        return

    user_data[field] = v

    if field == "phone":
        if not user_data.get("contactId") and not user_data.get("contact_id"):
            new_id = str(uuid.uuid4())
            user_data["contactId"] = new_id
            user_data["contact_id"] = new_id

    save_user_data(user_data)


def fetch_housing_types():
    """جلب أنواع السكن من الـ API (قائمة من كائنات {key, value})"""
    try:
        resp = requests.get(HOUSING_API, timeout=10)
        if resp.status_code == 200:
            j = resp.json()
            return j.get("data", [])
        return None
    except Exception:
        return None


def set_housing_selection(value):
    user_data = load_user_data()
    if not value:
        return False, None
    v = value.strip()
    types = fetch_housing_types() or []
    matched = next((t for t in types if str(t.get("value", "")).strip() == v), None)
    if not matched:
        matched = next((t for t in types if t.get("value", "").strip().lower() == v.lower()), None)
    if matched:
        user_data["housing_key"] = matched.get("key")
        user_data["housing_value"] = matched.get("value")
        if str(matched.get("value", "")).strip() == "فيلا":
            user_data["is_villa"] = True
        else:
            user_data.pop("is_villa", None)
        if user_data.get("pending_field") == "housing":
            user_data.pop("pending_field", None)
        save_user_data(user_data)
        try:
            save_address_snapshot(user_data)
        except Exception:
            pass
        return True, matched
    return False, None
 
def create_lead_hourly(pending_query=None, description=None):
    """ارسال بيانات المستخدم إلى API الخاص بإنشاء Lead بنظام hourly.
    ترجع (ok, response_message, sent_body).
    """
    try:
        user_data = load_user_data()

        url = HOURLYLEAD_API

        body = {
            "description": description or f"طلب وارد من البوت (خيار: {pending_query})",
            # بعض الحقول قد لا تكون متوفرة في ملف user_data لذا نبحث بعدة مفاتيح
            "contactId": user_data.get("contactId") or user_data.get("contact_id") or None,
            "cityId": user_data.get("city_id") or user_data.get("cityId") or None,
            "contactName": user_data.get("name"),
            "phoneNumber": user_data.get("phone"),
            "districtId": user_data.get("district_id") or user_data.get("districtId") or None,
            "serviceId": None,
            # newly added fields
            "email": user_data.get("email"),
            "nationalId": user_data.get("national_id") or user_data.get("nationalId"),
        }

        headers = {"Content-Type": "application/json"}

        resp = requests.post(url, json=body, headers=headers, timeout=15)

        try:
            j = resp.json()
        except Exception:
            j = None

        if resp.status_code == 200:
            # مسح الحالة المعلقة لأن الطلب تم إرساله بنجاح
            if user_data.get("pending_action"):
                user_data.pop("pending_action", None)
            if user_data.get("pending_query"):
                user_data.pop("pending_query", None)
            save_user_data(user_data)

            resp_msg = None
            if isinstance(j, dict) and j.get("data"):
                resp_msg = str(j.get("data"))


            return True, resp_msg, body
        else:
            # لا نمسح الحالة المعلقة حتى يتم المحاولة لاحقاً
            msg = f"فشل إرسال الطلب، رمز الحالة: {resp.status_code}"
            if j and isinstance(j, dict) and j.get("message"):
                msg += f" - {j.get('message')}"
            return False, msg, body

    except Exception as e:
        return False, f"حدث خطأ أثناء إرسال الطلب: {e}", None

# إضافة دالة لبناء الـ URL في وقت التشغيل
def _build_add_address_api():
    fp = _load_fixed_package()
    service_id = fp.get("service_id") or fp.get("serviceId") or ""
    step_id = fp.get("stepId") or fp.get("step_id") or ""
    base = "https://erp.rnr.sa:8005/ar/api/HourlyContract/AddNewAddress"
    return f"{base}?hourlyServiceId={service_id}&stepId={step_id}"

# --- new: generate a bearer-like token for testing (or reuse saved one) ---
def _generate_bearer_token():
    """Generates a long random token string and returns 'bearer <token>'.

    Uses secrets.token_urlsafe to create a URL-safe long random string.
    """
    # length parameter: control entropy; token_urlsafe(n) yields ~ceil(n*4/3) chars
    token = secrets.token_urlsafe(384)  # produce a long token similar to example
    return "bearer " + token

def _ensure_auth_token_in_user_data():
    """Return an Authorization header value or None.
    Do NOT generate a new token. Normalize existing values (strip quotes,
    ensure "bearer " prefix if raw token provided) and persist. If nothing
    usable is found, return None so caller can omit the header.
    """
    user_data = load_user_data()
    existing = user_data.get("auth_token")
    if existing:
        ex = str(existing).strip()
        # remove surrounding quotes if present
        if len(ex) >= 2 and ((ex[0] == '"' and ex[-1] == '"') or (ex[0] == "'" and ex[-1] == "'")):
            ex = ex[1:-1].strip()
        if not ex:
            return None
        if ex.lower().startswith("bearer "):
            # keep normalized form
            user_data["auth_token"] = ex
            save_user_data(user_data)
            return ex
        # raw token -> prefix and persist
        bearer = "bearer " + ex
        user_data["auth_token"] = bearer
        save_user_data(user_data)
        return bearer

    # try to read Authorization from existing SaveAddrease.json (if someone wrote it)
    try:
        save_path = os.path.join(os.path.dirname(__file__), "..", "SaveAddrease.json")
        if os.path.exists(save_path):
            with open(save_path, "r", encoding="utf-8") as f:
                try:
                    payload = json.load(f)
                    if isinstance(payload, dict):
                        headers = payload.get("headers") or {}
                        auth_from_file = headers.get("Authorization") or headers.get("authorization")
                        if auth_from_file:
                            auth_from_file = str(auth_from_file).strip()
                            if len(auth_from_file) >= 2 and ((auth_from_file[0] == '"' and auth_from_file[-1] == '"') or (auth_from_file[0] == "'" and auth_from_file[-1] == "'")):
                                auth_from_file = auth_from_file[1:-1].strip()
                            if not auth_from_file:
                                return None
                            if auth_from_file.lower().startswith("bearer "):
                                user_data["auth_token"] = auth_from_file
                                save_user_data(user_data)
                                return auth_from_file
                            else:
                                bearer_val = "bearer " + auth_from_file
                                user_data["auth_token"] = bearer_val
                                save_user_data(user_data)
                                return bearer_val
                except Exception:
                    pass
    except Exception:
        pass

    # Do not generate a token; caller must omit Authorization if None
    return None

def send_address_to_api(body):
    """
    يرسل العنوان إلى السيرفر ويطبع URL, BODY, HEADERS, STATUS, RESPONSE منسقاً.
    يعيد: (status_code, resp_json, url, headers)
    """
    url = _build_add_address_api()
    try:
        auth_header = _ensure_auth_token_in_user_data()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "platform": "android",
            "version": "7.0.0",
            "source": "1",
        }
        # only include Authorization if we have a non-empty token
        if auth_header:
            headers["Authorization"] = auth_header

        user_data = load_user_data()
        if user_data.get("firebaseDeviceId"):
            headers["firebaseDeviceId"] = user_data.get("firebaseDeviceId")
        if user_data.get("playerId"):
            headers["playerId"] = user_data.get("playerId")
        if user_data.get("isOutSA") is not None:
            headers["isOutSA"] = str(user_data.get("isOutSA")).lower()

        # طباعة واضحة ومنسقة
        print("┌─ Request to AddNewAddress")
        print("│ URL: " + url)
        try:
            print("│ BODY:\n" + json.dumps(body, ensure_ascii=False, indent=2))
        except Exception:
            print("│ BODY: (unable to pretty-print) -", body)
        try:
            # indicate whether Authorization is being sent
            headers_print = dict(headers)
            if "Authorization" not in headers_print:
                headers_print["Authorization"] = "(omitted)"
            print("│ HEADERS:\n" + json.dumps(headers_print, ensure_ascii=False, indent=2))
        except Exception:
            print("│ HEADERS: (unable to pretty-print) -", headers)
        print("└" + "─" * 40)

        resp = requests.post(url, json=body, headers=headers, timeout=15)

        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}

        # طباعة الحالة والرد بشكل منسق
        print("┌─ Response")
        print(f"│ Status: {resp.status_code}")
        try:
            print("│ Response Body:\n" + json.dumps(resp_json, ensure_ascii=False, indent=2))
        except Exception:
            print("│ Response Body: (unable to pretty-print) -", resp_json)
        print("└" + "─" * 40)

        return resp.status_code, resp_json, url, headers
    except Exception as e:
        print("Error while sending request to:", url)
        print("Exception:", str(e))
        return None, {"error": str(e)}, url, {}

def save_address_snapshot(user_data: dict, resp_json=None, status_code=None):
    """
    يبني body من user_data، يرسلها إلى الـ API، ويحفظ
    request/response/status_code/url/headers في SaveAddrease.json.
    إذا كان في SaveAddrease.json contactId محدث، نفضّله على user_data.
    """
    try:
        # إذا كان في SaveAddrease.json contactId محدث، نفضّله على user_data
        save_path = os.path.join(os.path.dirname(__file__), "..", "SaveAddrease.json")
        try:
            if os.path.exists(save_path):
                with open(save_path, "r", encoding="utf-8") as sf:
                    existing_payload = json.load(sf) or {}
                    req_sec = existing_payload.get("request") or {}
                    file_contact = req_sec.get("contactId")
                    if file_contact:
                        # نستخدم قيمة contactId من الملف إن وُجدت وصالحة (غير فارغة)
                        user_data = dict(user_data)  # shallow copy to avoid mutating caller
                        user_data["contactId"] = file_contact
                        user_data["contact_id"] = file_contact
        except Exception:
            # إذا فشل قراءة الملف نتجاهل ونتابع مع user_data كما هو
            pass

        contact_id = user_data.get("contactId") or user_data.get("contact_id")
        body = {
            "contactId": contact_id,
            "addressNotes": user_data.get("addressNotes") or user_data.get("address_notes") or "",
            "houseNo": user_data.get("houseNo") or user_data.get("house_no") or "",
            "houseType": str(user_data.get("housing_key"))
            if user_data.get("housing_key") is not None
            else (str(user_data.get("houseType")) if user_data.get("houseType") is not None else "0"),
            "floorNo": user_data.get("floorNo") or user_data.get("floor_no") or "0",
            "apartmentNo": user_data.get("apartmentNo") or user_data.get("apartment_no") or "0",
            "cityId": user_data.get("city_id") or user_data.get("cityId") or None,
            "districtId": user_data.get("district_id") or user_data.get("districtId") or None,
            "latitude": str(user_data.get("latitude") or ""),
            "longitude": str(user_data.get("longitude") or ""),
            "type": 2,
        }

        # إرسال فعلي للـ API — ترجع أيضًا الـ URL المستخدم و headers
        status_code, resp_json, url, used_headers = send_address_to_api(body)

        payload = {
            "request": body,
            "response": resp_json,
            "status_code": status_code,
            "saved_at": datetime.datetime.utcnow().isoformat() + "Z",
            "url": url,
            "headers": used_headers,  # حفظ الـ headers التي استخدمت أثناء الطلب
        }

        # كتابة آمنة (atomic replace)
        tmp_path = save_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        try:
            os.replace(tmp_path, save_path)
        except Exception:
            # إذا فشل الاستبدال استخدم كتابة مباشرة كاحتياط
            with open(save_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"✅ تم إرسال العنوان وتخزين الرد في {save_path} (Status {status_code})")
        try:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception:
            print(payload)
        return True
    except Exception as e:
        err_path = os.path.join(os.path.dirname(__file__), "..", "SaveAddrease_error.log")
        with open(err_path, "a", encoding="utf-8") as ef:
            ef.write(f"{datetime.datetime.utcnow().isoformat()} - save_address_snapshot error: {e}\n")
        return False

def _get_step_id_for_complete_profile() -> str:
    """Return stepId to use for CompleteProfile URL. Prefer fixedPackage.json stepId, else empty."""
    try:
        fp = _load_fixed_package()
        return fp.get("stepId") or fp.get("step_id") or ""
    except Exception:
        return ""

def complete_profile() -> tuple[bool, Optional[int], Dict[str, Any]]:
    """
    Sends a POST to CompleteProfile using saved user_data.
    Returns (ok: bool, status_code: int or None, resp_json: dict or raw text container).
    On success (status 200) sets user_data['profile_completed'] = True.
    """
    try:
        user_data = load_user_data()
        contact_id = user_data.get("contactId") or user_data.get("contact_id")
        if not contact_id:
            return False, None, {"error": "missing contactId"}

        # Determine nationalityId: prefer nationaliy_key saved by selection, else try any alt keys
        nationality_id = user_data.get("nationality_key") or user_data.get("nationalityId") or user_data.get("nationality_id") or ""

        # Map gender stored as Arabic to expected numeric codes (fallback to existing numeric)
        g = user_data.get("gender", "")
        gender_map = {"ذكر": "1", "أنثى": "2", "male": "1", "female": "2", "m": "1", "f": "2"}
        gender_val = gender_map.get(str(g).strip().lower(), str(g))

        body = {
            "contactId": contact_id,
            "jobTitle": user_data.get("jobTitle") or "",
            "email": user_data.get("email") or "",
            "nationalityId": nationality_id,
            "phoneNumber": user_data.get("phone") or "",
            "otherMobilePhone": user_data.get("otherMobilePhone") or "",
            "gender": gender_val,
            "idNumber": user_data.get("national_id") or user_data.get("idNumber") or ""
        }

        # Build URL using stepId from fixedPackage.json if available; fallback to empty stepId
        step_id = _get_step_id_for_complete_profile()
        url = f"https://erp.rnr.sa:8005/ar/api/Contact/CompleteProfile?serviceType=2&stepId={step_id}&stepType=2"

        # Build headers: prefer normalized auth_token if present, and include other optional headers from SaveAddrease.json
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/plain, */*"}
        
        auth = _ensure_auth_token_in_user_data()
        if auth:
            headers["Authorization"] = auth

        # Try to preserve firebaseDeviceId/playerId/isOutSA if available in user_data
        if user_data.get("firebaseDeviceId"):
            headers["firebaseDeviceId"] = user_data.get("firebaseDeviceId")
        if user_data.get("playerId"):
            headers["playerId"] = user_data.get("playerId")
        if user_data.get("isOutSA") is not None:
            headers["isOutSA"] = str(user_data.get("isOutSA")).lower()

        # NEW: حفظ لقطة مبدئية قبل إرسال الطلب (RESPONSE/HEADERS/STATUS_CODE = null)
        try:
            save_complete_profile_snapshot(url=url, body=body, response=None, headers=None, status_code=None)
        except Exception:
            # إذا فشل الحفظ المبدئي لا نوقف العملية
            pass

        # Do the request
        resp = requests.post(url, json=body, headers=headers, timeout=15)

        # parse response safely before saving snapshot
        try:
            resp_json = resp.json()
        except Exception:
            resp_json = {"raw_text": resp.text}

        # تحديث اللقطة بعد استلام الرد (سيحفظ RESPONSE, HEADERS, STATUS_CODE)
        try:
            save_complete_profile_snapshot(
                url=url,
                body=body,
                response=resp_json,
                headers=headers,
                status_code=resp.status_code
            )
        except Exception:
            # لا نوقف التنفيذ إذا فشل الحفظ
            pass

        if resp.status_code == 200:
            # mark completed
            ud = load_user_data()
            ud["profile_completed"] = True
            save_user_data(ud)
            return True, resp.status_code, resp_json
        else:
            return False, resp.status_code, resp_json

    except Exception as e:
        return False, None, {"error": str(e)}
COMPLEAT_PROFILE_LOG = os.path.join(os.path.dirname(__file__), "..", "compleatProfile.json")

def save_complete_profile_log(url, body, response, headers, status_code):
    """
    يحفظ الـ CompleteProfile.
    """
    log_text = (
        f"STATUS CODE {status_code}\n"
        f"URL => {url}\n"
        f"BODY => {json.dumps(body, ensure_ascii=False, indent=2)}\n"
        f"RESPONSE => {json.dumps(response, ensure_ascii=False, indent=2)}\n"
        f"HEADER => {json.dumps(headers, ensure_ascii=False, indent=2)}\n"
        f"saved_at => {datetime.datetime.utcnow().isoformat()}Z\n"
    )

    # الكتابة كنص وليس JSON منسق
    with open(COMPLEAT_PROFILE_LOG, "w", encoding="utf-8") as f:
        f.write(log_text)

# إضافة ثابت لمسار الملف المطلوب حفظه
COMPLETE_PROFILE_SNAPSHOT = os.path.join(os.path.dirname(__file__), "..", "CompleteProfile.json")
def prepare_complete_profile_payload() -> tuple[str, Dict[str, typing.Any]]:
    """
    يبني ويُرجع (url, body) لاستخدامها في حفظ لقطة CompleteProfile دون إرسال الطلب.
    يعكس منطق بناء الـ body الموجود في complete_profile().
    """
    user_data = load_user_data()
    contact_id = user_data.get("contactId") or user_data.get("contact_id") or ""
    nationality_id = user_data.get("nationality_key") or user_data.get("nationalityId") or user_data.get("nationality_id") or ""
    g = user_data.get("gender", "")
    gender_map = {"ذكر": "1", "أنثى": "2", "male": "1", "female": "2", "m": "1", "f": "2"}
    gender_val = gender_map.get(str(g).strip().lower(), str(g))

    body = {
        "contactId": contact_id,
        "jobTitle": user_data.get("jobTitle") or "",
        "email": user_data.get("email") or "",
        "nationalityId": nationality_id,
        "phoneNumber": user_data.get("phone") or "",
        "otherMobilePhone": user_data.get("otherMobilePhone") or "",
        "gender": gender_val,
        "idNumber": user_data.get("national_id") or user_data.get("idNumber") or ""
    }

    step_id = _get_step_id_for_complete_profile()
    url = f"https://erp.rnr.sa:8005/ar/api/Contact/CompleteProfile?serviceType=2&stepId={step_id}&stepType=2"
    return url, body

def save_complete_profile_snapshot(url: str, body: Dict[str, typing.Any],
                                   response=None, headers=None, status_code=None) -> bool:
    """
    يحفظ URL و BODY و RESPONSE و HEADERS و STATUS في CompleteProfile.json
    """
    payload = {
        "URL": url,
        "BODY": body,
        "RESPONSE": response,
        "HEADERS": headers,
        "STATUS_CODE": status_code,
        "saved_at": datetime.datetime.utcnow().isoformat() + "Z"
    }

    try:
        tmp = COMPLETE_PROFILE_SNAPSHOT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        os.replace(tmp, COMPLETE_PROFILE_SNAPSHOT)
        return True

    except Exception as e:
        print("⚠️ فشل حفظ CompleteProfile.json:", e)
        return False

def _sync_complete_profile_snapshot_with_fixed_package() -> bool:
    """
    If CompleteProfile.json exists, update its URL's stepId from fixedPackage.json (if present).
    Returns True if an update was made.
    """
    try:
        fp = _load_fixed_package()
        step_id = fp.get("stepId") or fp.get("step_id") or ""
        if not step_id:
            return False

        if not os.path.exists(COMPLETE_PROFILE_SNAPSHOT):
            return False

        with open(COMPLETE_PROFILE_SNAPSHOT, "r", encoding="utf-8") as f:
            try:
                payload = json.load(f)
            except Exception:
                return False

        url = payload.get("URL") or ""
        if not url:
            return False

        # Parse and replace stepId query param
        try:
            import urllib.parse as _up
            parsed = _up.urlparse(url)
            qs = _up.parse_qs(parsed.query)
            qs['stepId'] = [step_id]
            new_q = _up.urlencode(qs, doseq=True)
            new_url = _up.urlunparse(parsed._replace(query=new_q))
        except Exception:
            # Fallback: naive replace of 'stepId=' value
            if "stepId=" in url:
                prefix, rest = url.split("stepId=", 1)
                # keep everything after next '&' if exists
                tail = ""
                if "&" in rest:
                    tail = "&" + rest.split("&", 1)[1]
                new_url = f"{prefix}stepId={step_id}{tail}"
            else:
                return False

        if new_url == url:
            return False

        payload['URL'] = new_url
        tmp = COMPLETE_PROFILE_SNAPSHOT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as tf:
            json.dump(payload, tf, ensure_ascii=False, indent=2)
        os.replace(tmp, COMPLETE_PROFILE_SNAPSHOT)
        print(f"✅ Updated CompleteProfile.json URL stepId -> {step_id}")
        return True
    except Exception as e:
        print("⚠️ _sync_complete_profile_snapshot_with_fixed_package failed:", e)
        return False

# Run sync on import so existing CompleteProfile.json is updated from fixedPackage.json
_try_sync = _sync_complete_profile_snapshot_with_fixed_package()