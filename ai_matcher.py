import os
import json
import difflib
from PIL import Image

def image_similarity(photo1, photo2):
    """రెండు ఫోటోల కలర్ హిస్టోగ్రామ్‌లను పోల్చి 0.0 నుండి 1.0 మధ్య స్కోర్ ఇస్తుంది."""
    if not photo1 or not photo2:
        return None
    if not os.path.isfile(photo1) or not os.path.isfile(photo2):
        return None

    try:
        with Image.open(photo1) as img1, Image.open(photo2) as img2:
            img1 = img1.convert("RGB").resize((128, 128))
            img2 = img2.convert("RGB").resize((128, 128))

            h1 = img1.histogram()
            h2 = img2.histogram()

            distance = sum(abs(a - b) for a, b in zip(h1, h2)) / 2
            max_distance = sum(h1)
            similarity = 1 - (distance / max_distance) if max_distance else 0
            return max(0.0, min(1.0, similarity))
    except Exception as exc:
        print("Image comparison error:", exc)
        return None


def fallback_verify(lost_item, lost_photo, found_photo, qa, found_desc):
    """Gemini API లేనప్పుడు టెక్స్ట్ మరియు ఫోటో పోలిక ఆధారంగా పనిచేసే ఫాల్‌బ్యాక్ వెరిఫికేషన్."""
    if not qa:
        return {
            "match": False,
            "confidence": 0.0,
            "reason": "No private verification questions are available."
        }

    scores = []
    for owner_answer, finder_answer in qa:
        owner_str = str(owner_answer or "").strip().lower()
        finder_str = str(finder_answer or "").strip().lower()

        if not owner_str or not finder_str:
            scores.append(0.0)
            continue

        score = difflib.SequenceMatcher(None, owner_str, finder_str).ratio()
        if owner_str in finder_str or finder_str in owner_str:
            score = max(score, 0.85)
        if owner_str == finder_str:
            score = 1.0

        scores.append(score)

    answer_score = sum(scores) / len(scores) if scores else 0.0
    photo_score = image_similarity(lost_photo, found_photo)

    confidence = answer_score
    if photo_score is not None:
        confidence = (answer_score * 0.80) + (photo_score * 0.20)

    matched_count = sum(s >= 0.60 for s in scores)
    is_match = answer_score >= 0.60 and matched_count >= max(1, (len(scores) + 1) // 2)

    if is_match:
        reason = f"Private answers matched satisfactorily ({matched_count}/{len(scores)} questions verified)."
    else:
        reason = "The supplied answers did not match sufficiently with the owner's private details."

    return {
        "match": bool(is_match),
        "confidence": round(confidence, 2),
        "reason": reason
    }


def verify(lost_item, lost_photo, found_photo, qa, found_desc):
    """క్లెయిమ్ వెరిఫికేషన్."""
    if not qa or any(not str(f or "").strip() for _, f in qa):
        return {
            "match": False,
            "confidence": 0.0,
            "reason": "Please answer all private verification questions."
        }

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if api_key:
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")

            parts = [
                """You are an expert lost-and-found verification assistant.
Compare the private question-answer pairs and photos carefully.
Return ONLY raw JSON with no Markdown wrappers:
{"match": true/false, "confidence": 0.0 to 1.0, "reason": "brief explanation"}""",
                f"Lost item name: {lost_item.get('name')}",
                f"Lost item category: {lost_item.get('category')}",
                f"Lost public description: {lost_item.get('description')}",
                f"Finder note: {found_desc}",
                f"QA comparisons: {json.dumps(qa)}"
            ]

            for path, label in ((lost_photo, "Lost Photo"), (found_photo, "Found Photo")):
                if path and os.path.isfile(path):
                    with Image.open(path) as img:
                        parts.append(f"{label}:")
                        parts.append(img.convert("RGB").copy())

            response = model.generate_content(parts)
            raw = response.text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            result = json.loads(raw)
            conf = float(result.get("confidence", 0.0))
            return {
                "match": bool(result.get("match", False)) and conf >= 0.60,
                "confidence": round(conf, 2),
                "reason": str(result.get("reason", "AI verification completed."))
            }
        except Exception as exc:
            print("Gemini verification error, using fallback:", exc)

    return fallback_verify(lost_item, lost_photo, found_photo, qa, found_desc)


# --- FEATURE 5: REVERSE AI AUTO-MATCHING ENGINE ---
def reverse_auto_match(found_item, lost_items_list, uploads_dir):
    """
    దొరికిన వస్తువును గతంలో పోయిన అన్ని వస్తువులతో AI స్కాన్ చేసి, 
    అత్యధికంగా మ్యాచ్ అయ్యే పోస్ట్ మరియు స్కోర్‌ను వెతుకుతుంది.
    """
    if not lost_items_list:
        return None, 0.0, "No candidate lost items found."

    best_match_id = None
    best_score = 0.0
    best_reason = ""

    found_text = f"{found_item.get('name', '')} {found_item.get('category', '')} {found_item.get('place', '')} {found_item.get('description', '')}".lower()
    found_photo_path = os.path.join(uploads_dir, found_item.get('photo')) if found_item.get('photo') else None

    for item in lost_items_list:
        # 1. కేటగిరీ మ్యాచ్ బూస్ట్
        cat_match = (item['category'].lower() == found_item.get('category', '').lower())
        
        # 2. టెక్స్ట్ సిమిలారిటీ (పేరు, లొకేషన్, వర్ణన)
        lost_text = f"{item['name']} {item['category']} {item['place'] or ''} {item['description']}".lower()
        text_sim = difflib.SequenceMatcher(None, found_text, lost_text).ratio()
        
        # పేరు పదాలు మ్యాచ్ అయితే బోనస్
        found_words = set(found_item.get('name', '').lower().split())
        lost_words = set(item['name'].lower().split())
        overlap = found_words.intersection(lost_words)
        if overlap:
            text_sim = min(1.0, text_sim + 0.25)

        # 3. ఇమేజ్ సిమిలారిటీ
        lost_photo_path = os.path.join(uploads_dir, item['photo']) if item['photo'] else None
        photo_sim = image_similarity(lost_photo_path, found_photo_path)

        # స్కోర్ క్యాలిక్యులేషన్
        if photo_sim is not None:
            combined_score = (text_sim * 0.65) + (photo_sim * 0.35)
        else:
            combined_score = text_sim

        if cat_match:
            combined_score = min(1.0, combined_score + 0.15)

        if combined_score > best_score:
            best_score = combined_score
            best_match_id = item['id']
            best_reason = f"High similarity in {item['category']} description ('{item['name']}') lost at {item['place'] or 'Campus'}."

    best_score = round(best_score, 2)
    return best_match_id, best_score, best_reason