import random
import string
from datetime import datetime
from urllib.parse import quote_plus
from bson.objectid import ObjectId
from flask import Flask, render_template, request, redirect, url_for, session, flash
from pymongo import MongoClient

app = Flask(__name__)
app.secret_key = "change_this_secret_key_for_sessions"

# --- CONFIGURATION ---
ADMIN_PASSWORD = "admin123"  # Admin login password
DB_USERNAME = "quizuser"     # <-- Change if you used a different username
DB_PASSWORD = quote_plus("Quiz12345")  # <-- Change if you used a different password

MONGO_URI = f"mongodb+srv://{DB_USERNAME}:{DB_PASSWORD}@cluster0.fb5p5mz.mongodb.net/?retryWrites=true&w=majority"
DB_NAME = "coding_quiz_db"

client = MongoClient(MONGO_URI)
db = client[DB_NAME]

quizzes_col = db["quizzes"]
questions_col = db["questions"]
submissions_col = db["submissions"]


def generate_secret_code(length=6):
    chars = string.ascii_uppercase + string.digits
    while True:
        code = "".join(random.choices(chars, k=length))
        if not quizzes_col.find_one({"code": code}):
            return code


# ---------------- STUDENT ROUTES ---------------- #

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        student_name = request.form.get("student_name", "").strip()
        quiz_code = request.form.get("quiz_code", "").strip().upper()

        if not student_name or not quiz_code:
            flash("Please enter both your name and the secret quiz code.", "error")
            return redirect(url_for("index"))

        quiz = quizzes_col.find_one({"code": quiz_code})
        if not quiz:
            flash("Invalid secret code. Please check with the admin.", "error")
            return redirect(url_for("index"))

        existing = submissions_col.find_one({
            "quiz_code": quiz_code,
            "student_name": {"$regex": f"^{student_name}$", "$options": "i"}
        })
        if existing:
            flash("You have already submitted this quiz.", "error")
            return redirect(url_for("index"))

        session["student_name"] = student_name
        session["quiz_code"] = quiz_code
        return redirect(url_for("take_quiz", code=quiz_code))

    return render_template("index.html")


@app.route("/quiz/<code>", methods=["GET", "POST"])
def take_quiz(code):
    code = code.upper()
    if session.get("quiz_code") != code or not session.get("student_name"):
        return redirect(url_for("index"))

    quiz = quizzes_col.find_one({"code": code})
    if not quiz:
        flash("Quiz not found.", "error")
        return redirect(url_for("index"))

    questions = list(questions_col.find({"quiz_code": code}))

    if request.method == "POST":
        score = 0
        detailed_answers = []

        for q in questions:
            qid = str(q["_id"])
            selected = request.form.get(f"q_{qid}")
            selected_idx = int(selected) if selected is not None else -1
            is_correct = selected_idx == q["correct_option"]

            if is_correct:
                score += 1

            detailed_answers.append({
                "question": q["question"],
                "selected_option": q["options"][selected_idx] if 0 <= selected_idx < len(q["options"]) else "Not Answered",
                "correct_option": q["options"][q["correct_option"]],
                "is_correct": is_correct
            })

        submissions_col.insert_one({
            "quiz_code": code,
            "student_name": session["student_name"],
            "score": score,
            "total": len(questions),
            "answers": detailed_answers,
            "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })

        student_name = session.pop("student_name", "Student")
        session.pop("quiz_code", None)

        return render_template(
            "quiz.html",
            quiz=quiz,
            submitted=True,
            student_name=student_name,
            score=score,
            total=len(questions)
        )

    return render_template(
        "quiz.html",
        quiz=quiz,
        questions=questions,
        student_name=session["student_name"],
        submitted=False
    )


# ---------------- ADMIN ROUTES ---------------- #

@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST" and "admin_password" in request.form:
        if request.form.get("admin_password") == ADMIN_PASSWORD:
            session["is_admin"] = True
            return redirect(url_for("admin"))
        else:
            flash("Incorrect admin password.", "error")
            return redirect(url_for("admin"))

    if not session.get("is_admin"):
        return render_template("admin.html", logged_in=False)

    quizzes = list(quizzes_col.find().sort("created_at", -1))
    selected_code = request.args.get("code", "").upper()

    if not selected_code and quizzes:
        selected_code = quizzes[0]["code"]

    questions = list(questions_col.find({"quiz_code": selected_code})) if selected_code else []
    submissions = list(
        submissions_col.find({"quiz_code": selected_code}).sort([("score", -1), ("submitted_at", 1)])
    ) if selected_code else []

    return render_template(
        "admin.html",
        logged_in=True,
        quizzes=quizzes,
        selected_code=selected_code,
        questions=questions,
        submissions=submissions
    )


@app.route("/admin/create_quiz", methods=["POST"])
def create_quiz():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    title = request.form.get("title", "").strip()
    code = request.form.get("code", "").strip().upper()

    if not title:
        flash("Please enter a Quiz Title.", "error")
        return redirect(url_for("admin"))

    if not code:
        code = generate_secret_code()

    if quizzes_col.find_one({"code": code}):
        flash(f"Secret code '{code}' already exists. Try generating a new one.", "error")
        return redirect(url_for("admin", code=code))

    quizzes_col.insert_one({
        "code": code,
        "title": title,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })
    flash(f"Secret code '{code}' generated for '{title}'. Share it with all students!", "success")
    return redirect(url_for("admin", code=code))


@app.route("/admin/add_question", methods=["POST"])
def add_question():
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    quiz_code = request.form.get("quiz_code", "").strip().upper()
    question_text = request.form.get("question", "").strip()
    code_snippet = request.form.get("code_snippet", "").rstrip()
    options = [
        request.form.get("opt0", "").strip(),
        request.form.get("opt1", "").strip(),
        request.form.get("opt2", "").strip(),
        request.form.get("opt3", "").strip(),
    ]
    correct_option = int(request.form.get("correct_option", 0))

    if not quiz_code:
        flash("Please generate a Secret Quiz Code in Step 1 first.", "error")
        return redirect(url_for("admin"))

    if not question_text or not all(options):
        flash("Please fill in the question and all 4 answer options.", "error")
        return redirect(url_for("admin", code=quiz_code))

    questions_col.insert_one({
        "quiz_code": quiz_code,
        "question": question_text,
        "code_snippet": code_snippet,
        "options": options,
        "correct_option": correct_option
    })
    flash("Question and answer uploaded to MongoDB!", "success")
    return redirect(url_for("admin", code=quiz_code))


@app.route("/admin/delete_question/<qid>")
def delete_question(qid):
    if not session.get("is_admin"):
        return redirect(url_for("admin"))

    code = request.args.get("code", "")
    questions_col.delete_one({"_id": ObjectId(qid)})
    flash("Question deleted.", "success")
    return redirect(url_for("admin", code=code))


@app.route("/admin/logout")
def admin_logout():
    session.pop("is_admin", None)
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=7777, debug=True, threaded=True)