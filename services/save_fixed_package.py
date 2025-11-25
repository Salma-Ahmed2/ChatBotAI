"""Utilities to save selected service, nationality and shift into FixedPackage.json

This module refactors the previous single-file implementation by centralizing
file I/O, removing duplicated nationality-letter resolution logic, and keeping
the original Arabic messages and function signatures.
"""

from typing import Any, Dict, List, Optional
import re
import json
import os
import time
import requests
import logging

LOGGER = logging.getLogger(__name__)
LOG_FMT = "%(levelname)s: %(message)s"
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format=LOG_FMT)

 
def _normalize_arabic_digits(s: str) -> str:
    """Normalize Arabic-Indic and Eastern Arabic-Indic digits to ASCII digits.

    This ensures inputs like '1', '١' (U+0661) or '۱' (U+06F1) are treated the same.
    """
    if not isinstance(s, str):
        return s
    trans = {chr(0x0660 + i): str(i) for i in range(10)}
    trans.update({chr(0x06F0 + i): str(i) for i in range(10)})
    return s.translate(str.maketrans(trans))


def _extract_numeric_value(val: Any) -> Optional[str]:
    """
    يحاول استخراج رقم من الجملة بناءً على:
    - الأرقام المكتوبة (عربية/إنجليزية)
    - الأنماط اللغوية الشائعة مثل "عاملة واحدة"، "عاملتين"، "مرة واحدة"
    - بدون الاعتماد على الكلمات البسيطة فقط
    """

    if val is None:
        return None
    
    text = str(val).strip()
    text = _normalize_arabic_digits(text)

    import re

    # 1) لو فيه رقم مكتوب مباشرة
    m = re.search(r"(\d+)", text)
    if m:
        return m.group(1)

    # 2) فهم الجمل الشائعة (NLP بسيط)
    patterns = {
        r"(عاملة\s*واحدة|عامله\s*واحده|واحدة\s*عاملة)": "1",
        r"(عامل\s*واحد|عاملة\s*واحد)": "1",
        r"(عاملتين|عامليْن|عاملان|عامِلان)": "2",
        r"(مرتين|مرتان)": "2",
        r"(ثلاث\s*عاملات|ثلاث\s*زيارات|ثلاث)": "3",
        r"(اربع\s*عاملات|اربعة\s*عاملات|اربعة)": "4",
        r"(خمسة\s*عاملات|خمس\s*عاملات|خمسة)": "5",
        r"(مرة\s*واحدة)": "1",
        r"(مرة\s*ثانية)": "2",
        r"(نص\s*ساعة|نصف\s*ساعة)": "30",
        r"(ربع\s*ساعة)": "15",
    }

    for pattern, num in patterns.items():
        if re.search(pattern, text, re.IGNORECASE):
            return num

    return None


FIXED_PACKAGE_PATH = os.path.join(os.path.dirname(__file__), "..", "fixedPackage.json")
RESOURCEGROUPS_API = "https://erp.rnr.sa:8005/ar/api/ResourceGroup/GetResourceGroupsByService?serviceId={}"
FIXED_PACKAGE_API = "https://erp.rnr.sa:8005/ar/api/HourlyContract/FixedPackage"


def _read_json_file(path: str) -> Optional[Dict[str, Any]]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في قراءة الملف %s: %s", path, exc)
        return None


def _write_json_file(path: str, data: Dict[str, Any]) -> bool:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في حفظ الملف %s: %s", path, exc)
        return False


def _append_hourly_pricing_trace(path: str, trace: Dict[str, Any]) -> bool:
    """Append a trace object to a JSON array at `path`. Creates the file if missing."""
    try:
        existing = None
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception:
                existing = None

        if isinstance(existing, list):
            existing.append(trace)
            _write_json_file(path, existing)
            return True
        else:
            # create new list with this trace
            _write_json_file(path, [trace])
            return True
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في إضافة سجل HourlyPricing: %s", exc)
        return False


def read_fixed_package() -> Dict[str, Any]:
    """Return the contents of FixedPackage.json or an empty dict if missing."""
    data = _read_json_file(FIXED_PACKAGE_PATH)
    return data or {}


def write_fixed_package(updates: Dict[str, Any]) -> bool:
    """Update FixedPackage.json with given fields (merge with existing)."""
    pkg = read_fixed_package()
    pkg.update(updates)
    if _write_json_file(FIXED_PACKAGE_PATH, pkg):
        LOGGER.info("✅ تم حفظ البيانات في %s", FIXED_PACKAGE_PATH)
        # NEW: إذا تم تحديث stepId، حدث CompleteProfile.json وأعد إرسال الطلب
        if "stepId" in updates or "step_id" in updates:
            try:
                from .user_info_manager import on_fixed_package_stepid_updated
                on_fixed_package_stepid_updated()
            except Exception as e:
                LOGGER.warning("⚠️ فشل تحديث CompleteProfile.json بعد تغيير stepId: %s", e)
        return True
    return False


def save_nationality_to_package(nationality_key: Any, nationality_value: Any) -> bool:
    """تحديث ملف FixedPackage.json لإضافة الجنسية المختارة"""
    return write_fixed_package({"nationality_key": nationality_key, "nationality_value": nationality_value})


def save_shift_to_package(shift_key: Any, shift_value: Any) -> bool:
    """تحديث ملف FixedPackage.json لإضافة الموعد المختار"""
    return write_fixed_package({"shift_key": shift_key, "shift_value": shift_value})


def _save_snapshot_to_saveaddrease(package_data: Dict[str, Any]) -> bool:
    """حفظ نسخة مبسطة في SaveAddrease.json تحتوي hourlyServiceId و stepId
    نحتفظ أيضاً بـ headers أو contactId إذا وجدت في الملف الحالي.
    """
    try:
        save_path = os.path.join(os.path.dirname(__file__), "..", "SaveAddrease.json")
        existing = _read_json_file(save_path) or {}

        # حافظ على headers إن وُجدت
        headers = existing.get("headers") or {}

        # حفظ/المحافظة على الحقول في request إن كانت موجودة (مثل contactId)
        req = existing.get("request", {}) or {}
        contact_from_file = req.get("contactId") or req.get("contact_id")
        if contact_from_file:
            req["contactId"] = contact_from_file

        # ضع hourlyServiceId من package_data (ادعم مفاتيح بديلة) - لن نحفظه في 'request'
        service_id = package_data.get("service_id") or package_data.get("serviceId") or package_data.get("id") or ""

        # ضع stepId من package_data بدعم مفاتيح بديلة ("step", "step_id") - لن نحفظه في 'request'
        step_id = (
            package_data.get("stepId")
            or package_data.get("step_id")
            or package_data.get("step")
            or ""
        )

        # Fallback: إن لم يُعثر على stepId في package_data حاول قراءته من fixedPackage.json الموحد
        if not step_id:
            try:
                fp = _read_json_file(FIXED_PACKAGE_PATH) or {}
                step_id = fp.get("stepId") or fp.get("step_id") or fp.get("step") or step_id
            except Exception:
                pass

        # لا نضيف hourlyServiceId و stepId داخل الجسم (request). نترك 'req' كما هو
        # (فقط نحافظ على contactId و الحقول الأخرى الموجودة مسبقاً)

        # بنية بسيطة للملف تحاكي ما يحتاجه AddNewAddress (نحتفظ بالرد/حالة سابقة إن وُجدت)
        payload = {
            "request": req,
            "response": existing.get("response"),
            "status_code": existing.get("status_code"),
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "url": f"https://erp.rnr.sa:8005/ar/api/HourlyContract/AddNewAddress?hourlyServiceId={service_id}&stepId={step_id}",
            "headers": headers,
        }

        # نكتب اللقطة الأولية فوراً
        wrote = _write_json_file(save_path, payload)
        if not wrote:
            return False

        # NEW: فور كتابة الـ URL نطلب إرسال العنوان مباشرة ليجلب الرد ويحدّث SaveAddrease.json
        try:
            from .user_info_manager import load_user_data, save_address_snapshot

            user_data = load_user_data()
            # save_address_snapshot سيبني الـ body من user_data ويرسل الطلب باستخدام الـ URL المبني
            # ولن يضع hourlyServiceId/stepId في الـ body (تظهر في URL فقط)
            save_address_snapshot(user_data)
        except Exception as e:
            LOGGER.warning("⚠️ خطأ عند محاولة إرسال العنوان فوراً: %s", e)

        return True
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في حفظ SaveAddrease.json: %s", exc)
        return False


def save_fixed_package(service_data: Dict[str, Any]) -> Any:
    """حفظ بيانات الخدمة المختارة في ملف FixedPackage.json

    Returns either the formatted nationalities message (if any found) or True on success.
    """
    try:
        # keep any stepId provided by the service data (some APIs return this)
        step_id = service_data.get("stepId") or service_data.get("step_id") or service_data.get("step")

        package_data = {
            "service_id": service_data.get("id"),
            "service_name": service_data.get("name"),
            "selected_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

        if step_id is not None:
            # persist canonical key "stepId" so later calls include it
            package_data["stepId"] = step_id

        if not write_fixed_package(package_data):
            return False

        # NEW: بعد حفظ fixedPackage.json نحفظ أيضاً لقطة مبسطة في SaveAddrease.json
        try:
            saved = _save_snapshot_to_saveaddrease(package_data)
            if saved:
                LOGGER.info("✅ تم تحديث SaveAddrease.json بالـ hourlyServiceId و stepId")
        except Exception as e:
            LOGGER.warning("⚠️ فشل محاولة حفظ SaveAddrease.json: %s", e)

        # بعد حفظ الخدمة، نجلب الجنسيات المتاحة
        nationalities = get_available_nationalities(service_data.get("id"))
        if nationalities:
            return format_nationalities_message(nationalities)
        return True
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في حفظ الخدمة المختارة: %s", exc)
        return False

def save_selected_package(package: Dict[str, Any]) -> bool:
    """حفظ الباقة المختارة داخل fixedPackage.json"""
    try:
        data = {
            "selected_package": {
                "displayName": package.get("displayName"),
                "packagePrice": package.get("packagePrice"),
                "resourceGroupName": package.get("resourceGroupName"),
                "employeeNumberName": package.get("employeeNumberName"),
                "weeklyVisitName": package.get("weeklyVisitName"),
                "contractDurationName": package.get("contractDurationName"),
                "visitShiftName": package.get("visitShiftName"),
                "timeSlotDisplayName": package.get("timeSlotDisplayName"),
                "visitHours": package.get("visitHours"),
                "promotionCodeDescription": package.get("promotionCodeDescription"),
            }
        }
        return write_fixed_package(data)
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في حفظ الباقة المختارة: %s", exc)
        return False

def get_available_nationalities(service_id: Any) -> Optional[List[Dict[str, Any]]]:
    """جلب الجنسيات المتاحة للخدمة from remote API.

    Returns list of nationality dicts or None on error/no-data.
    """
    try:
        url = RESOURCEGROUPS_API.format(service_id)
        LOGGER.info("📡 جلب الجنسيات المتاحة للخدمة %s", service_id)
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            return response.json().get("data", [])
        LOGGER.warning("⚠️ خطأ في جلب الجنسيات: %s", response.status_code)
        return None
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في جلب الجنسيات: %s", exc)
        return None


def format_nationalities_message(nationalities: List[Dict[str, Any]]) -> str:
    """تنسيق رسالة عرض الجنسيات المتاحة"""
    if not nationalities:
        return "⚠️ عذراً، لا توجد جنسيات متاحة لهذه الخدمة حالياً."

    options = []
    for i, nat in enumerate(nationalities):
        letter = chr(65 + i)  # A, B, C...
        value = nat.get("value", "غير معروف")
        options.append(f"{letter}- {value}")

    message = "من فضلك اختر الجنسية المطلوبة للحصول على الباقات:\n\n" + "\n".join(options)
    return message


def _resolve_nationality_letter(service_id: Any, nationality_value: str) -> Optional[str]:
    """Return the letter (A, B, ...) corresponding to the selected nationality value.

    This centralizes the remote fetch and index lookup used in several places.
    """
    try:
        nationalities = get_available_nationalities(service_id)
        if not nationalities:
            return None
        for i, nat in enumerate(nationalities):
            if nat.get("value") == nationality_value:
                return chr(65 + i)
    except Exception:
        return None
    return None


def get_available_shifts(service_id: Any) -> Optional[List[Dict[str, Any]]]:
    """جلب المواعيد المتاحة للخدمة من الملف المحلي"""
    shifts_path = os.path.join(os.path.dirname(__file__), "..", "HourlyServicesShift.json")
    try:
        data = _read_json_file(shifts_path)
        if not data:
            return None
        # try both str and raw key lookups to be forgiving to JSON keys
        service_shifts = data.get(service_id) or data.get(str(service_id)) or {}
        return service_shifts.get("shifts", [])
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في جلب المواعيد: %s", exc)
        return None


def format_shifts_message(shifts: List[Dict[str, Any]]) -> str:
    """تنسيق رسالة عرض المواعيد المتاحة مع إضافة رمز الجنسية المختارة"""
    if not shifts:
        return "⚠️ عذراً، لا توجد مواعيد متاحة لهذه الخدمة حالياً."

    nationality_letter = None
    try:
        pkg = read_fixed_package()
        nationality_value = pkg.get("nationality_value")
        service_id = pkg.get("service_id")
        if nationality_value and service_id:
            nationality_letter = _resolve_nationality_letter(service_id, nationality_value)
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في قراءة الجنسية المختارة: %s", exc)

    options = []
    for shift in shifts:
        key = shift.get("key")
        value = shift.get("value", "غير معروف")
        if nationality_letter:
            options.append(f"{nationality_letter}{key}- {value}")
        else:
            options.append(f"{key}- {value}")

    message = "من فضلك اختر الموعد المناسب:\n\n" + "\n".join(options)
    return message


def handle_nationality_selection(choice: str, nationalities: List[Dict[str, Any]]) -> str:
    """معالجة اختيار الجنسية وحفظها"""
    try:
        choice = choice.upper().strip()
        if len(choice) != 1 or not "A" <= choice <= "Z":
            return "⚠️ اختيار غير صالح. الرجاء اختيار الحرف المناسب (مثل A أو B)"

        index = ord(choice) - ord("A")
        if index < 0 or index >= len(nationalities):
            return "⚠️ الجنسية المختارة غير موجودة في القائمة"

        selected_nationality = nationalities[index]
        nationality_key = selected_nationality.get("key")
        nationality_value = selected_nationality.get("value")

        if save_nationality_to_package(nationality_key, nationality_value):
            pkg = read_fixed_package()
            service_id = pkg.get("service_id")
            if service_id:
                shifts = get_available_shifts(service_id)
                if shifts:
                    shift_msg = format_shifts_message(shifts)
                    return f"✅ تم اختيار الجنسية: {nationality_value}\n\n{shift_msg}"
            return f"✅ تم اختيار الجنسية: {nationality_value}"
        else:
            return "⚠️ حدث خطأ في حفظ الجنسية المختارة"
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في معالجة اختيار الجنسية: %s", exc)
        return "⚠️ حدث خطأ في معالجة اختيار الجنسية"




def call_fixed_package_api() -> Optional[List[Dict[str, Any]]]:
    """استدعاء API للحصول على باقات FixedPackage باستخدام القيم من fixedPackage.json"""
    try:
        pkg = read_fixed_package()
        step_id = pkg.get("stepId") or pkg.get("step_id") or pkg.get("step")
        nationality_id = pkg.get("nationality_key") or pkg.get("nationalityId") or pkg.get("nationality_id")
        shift = pkg.get("shift_key") or pkg.get("shift")

        if not (step_id and nationality_id and shift is not None):
            LOGGER.warning("⚠️ معطيات FixedPackage ناقصة للاتصال بـ FixedPackage API")
            return None

        params = {"stepId": step_id, "nationalityId": nationality_id, "shift": shift}
        LOGGER.info("📡 استدعاء FixedPackage API مع params=%s", params)
        resp = requests.get(FIXED_PACKAGE_API, params=params, timeout=10)
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            return data.get("selectedPackages", [])
        LOGGER.warning("⚠️ FixedPackage API أعاد حالة: %s", resp.status_code)
        return None
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في استدعاء FixedPackage API: %s", exc)
        return None


import google.generativeai as genai
from typing import List, Dict, Any

def _pick_package_fields(pkg: Dict[str, Any]) -> Dict[str, Any]:
    """انتقاء الحقول المطلوبة من كائن الباقة"""
    return {
        "displayName": pkg.get("displayName"),
        "packagePrice": pkg.get("packagePrice"),
        "promotionCode": pkg.get("promotionCode"),
        "resourceGroupName": pkg.get("resourceGroupName"),
        "employeeNumberName": pkg.get("employeeNumberName"),
        "weeklyVisitName": pkg.get("weeklyVisitName"),
        "contractDurationName": pkg.get("contractDurationName"),
        "visitShiftName": pkg.get("visitShiftName"),
        "timeSlotDisplayName": pkg.get("timeSlotDisplayName"),
        "visitHours": pkg.get("visitHours"),
        "promotionCodeDescription": pkg.get("promotionCodeDescription"),
    }


def format_packages_message(packages: List[Dict[str, Any]]) -> str:
    """جعل الذكاء الاصطناعي يقوم بتنسيق وعرض الباقات بدون أي تدخل"""

    model = genai.GenerativeModel(model_name="models/gemini-2.5-pro")

    if not packages:
        return "⚠️ عذراً، لم يتم العثور على باقات متاحة."

    cleaned_packages = [_pick_package_fields(p) for p in packages]

    prompt = """
أنت الآن مساعد متخصص في تنسيق وعرض الباقات للعملاء.
المطلوب:

- اعرض الباقات بشكل مرتب وواضح.
- قم بترقيم الباقات (1) (2) (3) ...
- لا تستخدم أي رموز مثل * أو - أو •
- فقط استخدم فواصل وأسطر جديدة.
- اللغة عربية بسيطة مفهومة.

صيغة العرض المرغوبة:

(1) اسم الباقة
السعر: ...
عدد الموظفين: ...
مدة العقد: ...
وهكذا بنفس الترتيب لكل باقة.

هذه هي بيانات الباقات:
"""

    prompt += str(cleaned_packages)

    response = model.generate_content(prompt)
    return response.text.strip()
def handle_shift_selection(choice: str, shifts: List[Dict[str, Any]]) -> str:
    """معالجة اختيار الموعد وحفظه - يقبل الإدخال بشكل رقم فقط أو حرف+رقم مثل A1"""
    try:
        choice = choice.strip()
        pkg = read_fixed_package()
        nationality_value = pkg.get("nationality_value")
        service_id = pkg.get("service_id")
        nationality_letter = None
        if nationality_value and service_id:
            nationality_letter = _resolve_nationality_letter(service_id, nationality_value)

        # التعامل مع الإدخال سواء كان رقماً فقط أو حرف+رقم
        if len(choice) > 1 and choice[0].isalpha():
            input_letter = choice[0].upper()
            if nationality_letter and input_letter != nationality_letter:
                return f"⚠️ الحرف {input_letter} غير صحيح. الجنسية المختارة هي {nationality_letter}"
            try:
                shift_num = int(choice[1:])
            except ValueError:
                return "⚠️ اختيار غير صالح. الرجاء اختيار الموعد بالشكل الصحيح (مثل A1 أو 1)"
        else:
            try:
                shift_num = int(choice)
            except ValueError:
                return "⚠️ اختيار غير صالح. الرجاء اختيار الموعد بالشكل الصحيح (مثل A1 أو 1)"

        selected_shift = next((s for s in shifts if s.get("key") == shift_num), None)
        if not selected_shift:
            return "⚠️ الموعد المختار غير موجود في القائمة"

        shift_key = selected_shift.get("key")
        shift_value = selected_shift.get("value")

        if save_shift_to_package(shift_key, shift_value):
            # بعد حفظ الموعد: استدعاء API FixedPackage لجلب الباقات ثم طباعة النتيجة
            try:
                # محاولة استدعاء API الباقات
                packages = call_fixed_package_api()
                # تحديث user_data.json وإضافة pending_query = الباقات
                try:
                    from .user_info_manager import load_user_data, save_user_data
                    ud = load_user_data()
                    ud["pending_query"] = "الباقات"
                    save_user_data(ud)
                except Exception as e:
                    LOGGER.warning("⚠️ خطأ في تحديث pending_query داخل user_data.json: %s", e)

                packages_msg = format_packages_message(packages) if packages is not None else "⚠️ تعذر جلب بيانات الباقات."
                # حاول أيضاً حفظ/إرسال العنوان كما كان سابقاً (إن أمكن)
                try:
                    from .user_info_manager import load_user_data, save_address_snapshot
                    user_data = load_user_data()
                    result = save_address_snapshot(user_data)
                    LOGGER.info("Called ADD_ADDRESS_API, result: %s", result)
                except Exception as e:
                    LOGGER.warning("⚠️ خطأ عند استدعاء ADD_ADDRESS_API: %s", e)

                return f"✅ تم اختيار الموعد: {shift_value}\n\n{packages_msg}"
            except Exception as e:
                LOGGER.warning("⚠️ خطأ عند جلب الباقات: %s", e)
                return f"✅ تم اختيار الموعد: {shift_value}\n\n⚠️ حدث خطأ في جلب بيانات الباقات"
        else:
            return "⚠️ حدث خطأ في حفظ الموعد المختار"
    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في معالجة اختيار الموعد: %s", exc)
        return "⚠️ حدث خطأ في معالجة اختيار الموعد"
    
def format_single_package(pkg: Dict[str, Any]) -> str:
    """عرض باقة واحدة بشكل واضح"""
    if not pkg:
        return "⚠️ الباقة غير موجودة."

    return f"""
اسم الباقة: {pkg.get("displayName")}
السعر: {pkg.get("packagePrice")}
عدد الموظفين: {pkg.get("employeeNumberName")}
عدد الزيارات الأسبوعية: {pkg.get("weeklyVisitName")}
مدة العقد: {pkg.get("contractDurationName")}
موعد الزيارة: {pkg.get("visitShiftName")}
عدد ساعات الزيارة: {pkg.get("visitHours")}
كود الخصم: {pkg.get("promotionCode")}
الوصف: {pkg.get("promotionCodeDescription") or "—"}
""".strip()

def handle_package_selection(choice: str) -> str:
    """معالجة اختيار الباقة بناءً على رقم، وحفظها في fixedPackage.json وuser_data.json"""
    try:
        index = int(choice.strip()) - 1
    except Exception:
        return "⚠️ من فضلك ادخل رقم صحيح لاختيار الباقة."

    packages = call_fixed_package_api()
    if not packages:
        return "⚠️ لا توجد باقات متاحة حالياً."

    if index < 0 or index >= len(packages):
        return "⚠️ الرقم خارج نطاق الباقات المتاحة."

    selected = packages[index]
    msg = format_single_package(selected)  # هذا لا يعرض selectedHourlyPricingId

    try:
        # حفظ الباقة كاملة في fixedPackage.json
        saved = save_selected_package(selected)
        if saved:
            LOGGER.info("✅ تم حفظ الباقة المختارة داخل fixedPackage.json")
        else:
            LOGGER.warning("⚠️ لم يتم حفظ الباقة المختارة")
    except Exception as e:
        LOGGER.warning("⚠️ خطأ عند حفظ الباقة المختارة: %s", e)

    # حفظ selectedHourlyPricingId في user_data.json
    try:
        from .user_info_manager import load_user_data, save_user_data
        ud = load_user_data()
        ud["selectedHourlyPricingId"] = selected.get("selectedHourlyPricingId")
        save_user_data(ud)
        LOGGER.info("✅ تم حفظ selectedHourlyPricingId في user_data.json")
    except Exception as e:
        LOGGER.warning("⚠️ خطأ عند حفظ selectedHourlyPricingId في user_data.json: %s", e)

    # تحديث pending_query داخل user_data.json (ليس حرجًا إن فشل)
    try:
        ud = load_user_data()
        ud["pending_query"] = "الباقات"
        save_user_data(ud)
    except Exception as e:
        LOGGER.warning("⚠️ خطأ في تحديث pending_query داخل user_data.json: %s", e)

    # جلب TimeSlot وإعداد رسالة مناسبة
    try:
        slots = call_time_slot_api()
        if slots is None:
            slot_msg = "⚠️ لم نتمكن من جلب المواعيد."
        else:
            slot_msg = format_timeslot_message(slots)
    except Exception as e:
        LOGGER.warning("⚠️ خطأ عند جلب TimeSlot: %s", e)
        slot_msg = "⚠️ حدث خطأ أثناء جلب المواعيد."
        try:
            from .user_info_manager import load_user_data, save_user_data
            ud = load_user_data()
            ud["pending_query"] = "timeslot_date"
            save_user_data(ud)
        except Exception as e:
            LOGGER.warning("⚠️ خطأ في تحديث pending_query للمرحلة timeslot_date: %s", e)

    return f"✅ تم اختيار الباقة رقم {choice}\n\n{msg}\n\n{slot_msg}"

def call_time_slot_api() -> Optional[List[Dict[str, Any]]]:
    """استدعاء API لجلب TimeSlot بعد اختيار الباقة وحفظ المفتاح داخليًا"""
    try:
        pkg = read_fixed_package()

        service_id = pkg.get("service_id")
        step_id = pkg.get("stepId")
        shift = pkg.get("shift_key")
        selected_pkg = pkg.get("selected_package")

        if not service_id or not step_id or shift is None or not selected_pkg:
            LOGGER.warning("⚠️ بيانات ناقصة لجلب TimeSlot")
            return None

        hours = selected_pkg.get("visitHours")
        if not hours:
            LOGGER.warning("⚠️ visitHours غير موجودة داخل selected_package")
            return None

        url = "https://erp.rnr.sa:8005/ar/api/HourlyTimeSlot/GetTimeSlotByServiceIdForDDL"
        body = {
            "serviceId": service_id,
            "stepId": step_id,
            "shift": str(shift),
            "hours": str(hours)
        }

        from .user_info_manager import load_user_data, save_user_data
        ud = load_user_data()
        token = ud.get("auth_token")

        headers = {
            "Authorization": token,
            "content-type": "application/json"
        }

        LOGGER.info("📡 استدعاء TimeSlot API: %s", body)
        resp = requests.post(url, json=body, headers=headers, timeout=10)

        if resp.status_code == 200:
            data = resp.json().get("data", [])

            # حفظ كل مفاتيح TimeSlot داخليًا بدون عرضها للعميل
            keys = [slot.get("key") for slot in data if "key" in slot]
            if keys:
                ud["time_slot_keys"] = keys
                save_user_data(ud)
                LOGGER.info("✅ تم حفظ TimeSlot keys داخل user_data.json")

            return data
        else:
            LOGGER.warning("⚠️ TimeSlot API status code: %s", resp.status_code)
            return None

    except Exception as exc:
        LOGGER.warning("⚠️ خطأ في استدعاء TimeSlot API: %s", exc)
        return None

def format_timeslot_message(slots: List[Dict[str, Any]]) -> str:
    if not slots:
        return "⚠️ لا توجد مواعيد متاحة لهذا الوقت."

    # عند عرض المواعيد نضع العلم بأننا ننتظر اختيار تاريخ بداية العقد
    try:
        from .user_info_manager import load_user_data, save_user_data
        ud = load_user_data()
        ud["pending_query"] = "timeslot_date"
        save_user_data(ud)
    except Exception:
        # لا نوقف التنفيذ إن فشل تحديث الحالة
        pass

    msg = "⏰ المواعيد المتاحة:\n\n"

    for i, slot in enumerate(slots, start=1):
        msg += f"{slot.get('value')}\n"
        msg += f"المدة المتاحة من :\n{slot.get('minDate')}\n إلى \n {slot.get('maxDate')}\n\n"

    msg += "من فضلك اختر التاريخ المناسب ويكون صياغته بهذا الشكل 29/12/2030."

    return msg.strip()
def fetch_available_days(contract_start_date: str) -> Optional[List[Dict[str, str]]]:
    """
    جلب الأيام المتاحة بعد إدخال تاريخ بداية العقد.
    يعتمد على stepId الموجود في fixedpackage.json.
    ويستخدم:
    - selectedHourlyPricingId من user_data.json
    - serviceId و resourceGroupId من fixedpackage.json
    - days = أول 5 أيام متتالية تبدأ من تاريخ العقد المختار
    """
    import datetime

    try:
        # ---- تحميل fixedPackage.json ----
        pkg = read_fixed_package()
        if not pkg:
            LOGGER.warning("⚠️ fixedPackage.json فارغ أو غير موجود")
            return None

        step_id = pkg.get("stepId")
        selected_pkg = pkg.get("selected_package")
        shift = pkg.get("shift_key")
        time_slot_id = pkg.get("time_slot_id")

        if not selected_pkg:
            LOGGER.warning("⚠️ selected_package غير موجود داخل fixedPackage.json")
            return None

        # ---- تحميل user_data.json ----
        from .user_info_manager import load_user_data, save_user_data
        ud = load_user_data()
        token = ud.get("auth_token")

        # جلب selectedHourlyPricingId من user_data.json
        user_selected_pricing_id = ud.get("selectedHourlyPricingId")
        time_slot_keys = ud.get("time_slot_keys") or []

        # ---- إنشاء DAYS: 5 أيام متتالية ----
        day, month, year = map(int, contract_start_date.split("/"))
        start_date = datetime.date(year, month, day)

        five_days = []
        for i in range(5):
            d = start_date + datetime.timedelta(days=i)
            five_days.append(d.strftime("%A"))  # اسم اليوم بالإنجليزي

        days_str = ",".join(five_days)

        # ---- تجهيز الرابط ----
        url = f"https://erp.rnr.sa:8005/ar/api/HourlyPricing/AvailableDaysWithDate?stepId={step_id}"

        # ---- تجهيز الـ PAYLOAD الصحيح ----
        # Determine a safe timeSlotId: prefer saved time_slot_keys, fallback to any
        # time_slot_id present in fixedPackage.json, otherwise None.
        chosen_time_slot_id = None
        if isinstance(time_slot_keys, (list, tuple)) and len(time_slot_keys) > 0:
            chosen_time_slot_id = time_slot_keys[0]
        elif time_slot_id:
            chosen_time_slot_id = time_slot_id

        if not chosen_time_slot_id:
            LOGGER.warning("⚠️ لم يتم العثور على timeSlotId في user_data أو fixedPackage.json")

        # Normalize fields to have numbers only where expected
        contract_duration_num = _extract_numeric_value(selected_pkg.get("contractDurationName")) or _extract_numeric_value(selected_pkg.get("contractDuration"))
        hours_count_num = _extract_numeric_value(selected_pkg.get("visitHours")) or _extract_numeric_value(selected_pkg.get("hoursCount")) or str(selected_pkg.get("visitHours") or "")
        empcount_num = _extract_numeric_value(selected_pkg.get("employeeNumberName")) or _extract_numeric_value(selected_pkg.get("employeeNumber"))
        weeklyvisits_num = _extract_numeric_value(selected_pkg.get("weeklyVisitName")) or _extract_numeric_value(selected_pkg.get("weeklyvisits"))

        payload = {
            "selectedHourlyPricingId": user_selected_pricing_id,                # من user_data.json
            "resourceGroupId": pkg.get("nationality_key"),                      # من fixedPackage.json
            "serviceId": pkg.get("service_id"),                                 # من fixedPackage.json
            "contractStartDate": contract_start_date,
            "contractDuration": contract_duration_num or "",
            "hoursCount": hours_count_num or "",
            "empcount": empcount_num or "",
            "weeklyvisits": weeklyvisits_num or "",
            "visitShift": str(shift),
            # "promotionCode": selected_pkg.get("promotionCode"),
            # "days": days_str,                                                   # 5 أيام فقط
            "timeSlotId": chosen_time_slot_id
        }

        headers = {
            "Authorization": token,
            "content-type": "application/json"
        }

        LOGGER.info(f"📡 استدعاء AvailableDaysWithDate API: {payload}")
        resp = requests.post(url, json=payload, headers=headers, timeout=10)

        # Try to parse JSON response safely
        resp_data = None
        resp_text = None
        try:
            resp_json = resp.json()
            resp_data = resp_json.get("data", resp_json)
        except Exception:
            resp_text = resp.text

        # Save a trace of the call (URL, request body, response, status) to availableDay.json
        try:
            save_path = os.path.join(os.path.dirname(__file__), "..", "availableDay.json")
            trace = {
                "url": url,
                "request": payload,
                "status_code": resp.status_code,
                "response_json": resp_data,
                "response_text": resp_text,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            _write_json_file(save_path, trace)
            LOGGER.info("✅ حفظت نتيجة AvailableDaysWithDate في %s", save_path)
        except Exception as e:
            LOGGER.warning("⚠️ خطأ عند حفظ availableDay.json: %s", e)

        if resp.status_code != 200:
            LOGGER.warning(f"⚠️ API status code: {resp.status_code}")
            return None

        data = resp_data if resp_data is not None else []

        # حفظ الأيام
        ud["available_days"] = data
        # سجل آخر تاريخ طلبه المستخدم حتى نتمكن من كشف "الإرسال مرة أخرى" لاحقًا
        try:
            ud["last_contract_start_date"] = contract_start_date
        except Exception:
            pass
        save_user_data(ud)

        return data

    except Exception as e:
        LOGGER.warning(f"⚠️ خطأ في fetch_available_days: {e}")
        return None
def select_preferred_days(chosen_numbers: List[int]) -> Optional[List[Dict[str, str]]]:
    """
    اختيار الأيام المفضلة بناءً على أرقام يختارها المستخدم.
    """
    try:
        from .user_info_manager import load_user_data, save_user_data
        ud = load_user_data()

        available = ud.get("available_days", [])
        if not available:
            return None

        # تحويل الأرقام المدخلة إلى إندكس (1 => 0)
        selected_days = []
        for n in chosen_numbers:
            index = n - 1
            if 0 <= index < len(available):
                selected_days.append(available[index])

        ud["selected_days"] = selected_days
        save_user_data(ud)

        return selected_days

    except Exception as e:
        LOGGER.warning(f"⚠️ خطأ في select_preferred_days: {e}")
        return None
def format_available_days_message(days: List[Dict[str, str]]) -> str:
    msg = "📅 الأيام المتاحة بناءً على التاريخ المختار:\n\n"

    for i, d in enumerate(days, start=1):
        msg += f"({i}) {d['dayName']} - {d['date']}\n"

    msg += "\n هل تريد التأكيد على اليوم المختار ام تريد اختيار يوم اخر من الأيام المفضلة ؟.\n"
    msg += "\n اخبرني بالتاريخ المناسب \n"
 

    return msg

def fetch_pricing_summary_with_ai(contract_date: str) -> str:
    """
    استدعاء API HourlyPricing/HourlyPricing
    ثم عرض تفاصيل الباقة المختارة + التاريخ + الأسعار بأسلوب AI
    """

    try:
        from .user_info_manager import load_user_data
        ud = load_user_data()
        selected_pricing_id = ud.get("selectedHourlyPricingId")

        pkg = read_fixed_package()
        selected_pkg = pkg.get("selected_package")

        if not selected_pricing_id or not selected_pkg:
            return "⚠️ لا يوجد باقة مختارة حالياً."

        # Prefer using time_slot keys stored in user_data (set by call_time_slot_api)
        time_slot_id = ud.get("time_slot_keys", [None])[0]

        payload = {
            "selectedHourlyPricingId": selected_pricing_id,
            "resourceGroupId": pkg.get("nationality_key"),
            "serviceId": pkg.get("service_id"),
            "contractStartDate": contract_date,
            "contractDuration": _extract_numeric_value(selected_pkg.get("contractDurationName")),
            "hoursCount": _extract_numeric_value(selected_pkg.get("visitHours")),
            "empcount": _extract_numeric_value(selected_pkg.get("employeeNumberName")),
            "weeklyvisits": _extract_numeric_value(selected_pkg.get("weeklyVisitName")),
            "visitShift": pkg.get("shift_key"),
            "promotionCode": selected_pkg.get("promotionCode"),
            # send the concrete date as the 'days' value (API expects date strings)
            "days": contract_date,
            "timeSlotId": time_slot_id
        }

        url = "https://erp.rnr.sa:8005/ar/api/HourlyPricing/HourlyPricing"
        headers = {
            "Authorization": ud.get("auth_token"),
            "content-type": "application/json"
        }

        LOGGER.info(f"📡 استدعاء Pricing Summary API: {payload}")
        resp = requests.post(url, json=payload, headers=headers, timeout=10)

        # Save a trace of the pricing call for debugging
        try:
            trace_path = os.path.join(os.path.dirname(__file__), "..", "hourlyPricing_summary.json")
            trace = {
                "url": url,
                "request": payload,
                "status_code": resp.status_code,
                "response_json": None,
                "response_text": None,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            try:
                trace_json = resp.json()
                trace["response_json"] = trace_json
            except Exception:
                trace["response_text"] = resp.text
            _write_json_file(trace_path, trace)
            LOGGER.info("✅ حفظت نتيجة Pricing Summary في %s", trace_path)
            # also append to unified HourlyPricing.json for easier inspection
            try:
                unified = os.path.join(os.path.dirname(__file__), "..", "HourlyPricing.json")
                _append_hourly_pricing_trace(unified, dict(trace))
                LOGGER.info("✅ أضفت السجل إلى %s", unified)
            except Exception:
                pass
        except Exception as e:
            LOGGER.warning("⚠️ خطأ عند حفظ سعر الباقة: %s", e)

        if resp.status_code != 200:
            return "⚠️ حدث خطأ أثناء جلب ملخص الأسعار."

        data = resp.json().get("data", {})
        packages = data.get("hourlyPackages", [])

        if not packages:
            return "⚠️ لم يتم العثور على بيانات الباقة."

        pkg_data = packages[0]

        # تجهيز AI
        model = genai.GenerativeModel("models/gemini-2.5-pro")

        prompt = f"""
أنت مساعد متخصص في تلخيص الباقات.
اعرض المعلومات التالية بشكل أنيق وواضح، وبنفس الأسلوب الذي استخدمته عند عرض الباقات سابقاً.

المعلومات:
اسم الباقة: {pkg_data.get("resourceGroupName")}
تاريخ البداية: {contract_date}
السعر قبل الخصم: {pkg_data.get("packagePrice")}
قيمة الخصم: {pkg_data.get("totalDiscountAmount")}
نسبة الخصم: {pkg_data.get("totalDiscountPercent")}%
السعر بعد الخصم: {pkg_data.get("packagePriceAfterTotalDiscount")}
الضريبة: {pkg_data.get("vatAmount")}
السعر النهائي: {pkg_data.get("finalPrice")}
عدد الساعات: {pkg_data.get("visitHours")}
عدد الزيارات الأسبوعية: {pkg_data.get("weeklyvisits")}
مدة العقد: {pkg_data.get("contractDuration")}
موعد الزيارة: {pkg_data.get("visitShift")}
        """

        resp_ai = model.generate_content(prompt)
        return resp_ai.text.strip()

    except Exception as e:
        LOGGER.warning(f"⚠️ خطأ في fetch_pricing_summary_with_ai: {e}")
        return "⚠️ حدث خطأ أثناء تجهيز تفاصيل الباقة."
def call_hourly_pricing_api(contract_start_date: str, chosen_day: str) -> Optional[Dict[str, Any]]:
    """
    استدعاء API HourlyPricing بعد أن يختار المستخدم التاريخ واليوم المفضل
    ثم إنشاء العقد النهائي تلقائياً.
    """
    try:
        from .user_info_manager import load_user_data
        ud = load_user_data()
        pkg = read_fixed_package()

        url = "https://erp.rnr.sa:8005/ar/api/HourlyPricing/HourlyPricing"
        step_id = pkg.get("stepId")

        # Normalize quantity-like fields to numeric values (no words)
        selected_pkg = pkg.get("selected_package", {})

        contract_duration_num = (
            _extract_numeric_value(selected_pkg.get("contractDurationName"))
            or _extract_numeric_value(selected_pkg.get("contractDuration"))
            or str(selected_pkg.get("contractDurationName") or "")
        )

        hours_count_num = (
            _extract_numeric_value(selected_pkg.get("visitHours"))
            or _extract_numeric_value(selected_pkg.get("hoursCount"))
            or str(selected_pkg.get("visitHours") or "")
        )

        empcount_num = (
            _extract_numeric_value(selected_pkg.get("employeeNumberName"))
            or _extract_numeric_value(selected_pkg.get("employeeNumber"))
            or str(selected_pkg.get("employeeNumberName") or "")
        )

        weeklyvisits_num = (
            _extract_numeric_value(selected_pkg.get("weeklyVisitName"))
            or _extract_numeric_value(selected_pkg.get("weeklyvisits"))
            or str(selected_pkg.get("weeklyVisitName") or "")
        )

        body = {
            "selectedHourlyPricingId": ud.get("selectedHourlyPricingId"),
            "resourceGroupId": pkg.get("nationality_key"),
            "serviceId": pkg.get("service_id"),
            "contractStartDate": contract_start_date,
            "contractDuration": contract_duration_num,
            "hoursCount": hours_count_num,
            "empcount": empcount_num,
            "weeklyvisits": weeklyvisits_num,
            "visitShift": pkg.get("shift_key"),
            "promotionCode": selected_pkg.get("promotionCode"),
            "days": chosen_day,
            "timeSlotId": ud.get("time_slot_keys", [None])[0]
        }

        params = {"stepId": step_id}

        resp = requests.post(url, params=params, json=body, timeout=10)

        # Save a trace for debugging
        try:
            trace_path = os.path.join(os.path.dirname(__file__), "..", "hourlyPricing.json")
            trace = {
                "url": url,
                "params": params,
                "request": body,
                "status_code": resp.status_code,
                "response_json": None,
                "response_text": None,
                "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
            }
            try:
                trace["response_json"] = resp.json()
            except Exception:
                trace["response_text"] = resp.text
            _write_json_file(trace_path, trace)
            LOGGER.info("✅ حفظت نتيجة HourlyPricing في %s", trace_path)

            # unified trace
            try:
                unified = os.path.join(os.path.dirname(__file__), "..", "HourlyPricing.json")
                _append_hourly_pricing_trace(unified, dict(trace))
            except Exception:
                pass

        except Exception as e:
            LOGGER.warning("⚠️ خطأ عند حفظ HourlyPricing: %s", e)

        # -------------------------------
        # ⚡ الخطوة الجديدة: إنشاء العقد
        # -------------------------------
        def create_contract(step_id_local):
            try:
                from .user_info_manager import load_user_data
                ud_local = load_user_data()

                url_c = f"https://erp.rnr.sa:8005/ar/api/HourlyContract/CreateContract?stepId={step_id_local}"

                headers_c = {
                    "Authorization": ud_local.get("auth_token"),
                    "content-type": "application/json"
                }

                resp_c = requests.post(url_c, headers=headers_c, json=None, timeout=10)

                # حفظ createContract.json
                save_path = os.path.join(os.path.dirname(__file__), "..", "createContract.json")
                trace_c = {
                    "url": url_c,
                    "status_code": resp_c.status_code,
                    "response": None,
                    "saved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ")
                }

                try:
                    trace_c["response"] = resp_c.json()
                except:
                    trace_c["response"] = resp_c.text

                _write_json_file(save_path, trace_c)

                if resp_c.status_code == 200:
                    return "🎉 تم إنشاء عقدك بنجاح"
                else:
                    return "⚠️ لم يتم إنشاء العقد."

            except Exception as e:
                return f"⚠️ خطأ أثناء إنشاء العقد: {e}"

        # -------------------------------
        # إذا التسعير نجح → ننشئ العقد
        # -------------------------------
        if resp.status_code == 200:
            final_msg = create_contract(step_id)
            return {"pricing": resp.json(), "contractMessage": final_msg}
        else:
            LOGGER.warning("⚠️ HourlyPricing API returned status: %s", resp.status_code)
            return None

    except Exception as e:
        LOGGER.warning("⚠️ Error calling HourlyPricing API: %s", e)
        return None


