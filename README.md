# Campus Find It 🎓🔍

Campus Find It is an AI-powered lost and found portal tailored for college campuses. It enables students to report lost personal items with private verification questions and lets finders verify ownership using Google Gemini AI or a smart local fallback algorithm without breaching owner privacy.

---

## 🚀 Key Features

1. **Strict Privacy Architecture**:
   - Owner contact details (Phone, Email) are never public.
   - Finder only sees owner contact after AI verifies answers & photo match.
   - Owner only sees finder contact after finder clicks "Confirm (Found)".
   - Admin view displays masked phones and emails (`98*****10`).
2. **Dynamic Category Questions**:
   - ID cards, Wallets, Books, Earphones, Phones, Bags, etc.
   - Custom question addition supported directly in UI.
3. **Dual Verification Pipeline**:
   - **Gemini AI**: Multi-modal photo check + semantic text QA reasoning.
   - **Built-in Fallback**: Normalized color histogram (`Pillow`) + text matching (`difflib`) when Gemini API is offline or key is omitted.
4. **Lifecycle State Machine**:
   - `lost` ➔ `matched` (AI match) ➔ `found` (finder confirms) ➔ `returned` (owner or admin confirms receipt).

---

## 🛠️ Step-by-Step Setup Guide (Windows / VS Code)

### Step 1: Open Folder in VS Code
Open VS Code, press `Ctrl + O` and open your project folder `campus_find_it`.

### Step 2: Open Terminal & Create Virtual Environment
Open the integrated terminal (`Ctrl + ~`) in VS Code and run:
```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1