from fastapi.testclient import TestClient

from app.main import app
from app.repositories.fake_resume_repository import get_fake_resume_repository

client = TestClient(app)


def profile() -> dict[str, str]:
    return {
        "full_name": "张三",
        "major": "计算机科学与技术",
        "grade": "大三",
        "contact": "13800000000",
    }


def skill(name: str, rating: int = 3) -> dict[str, object]:
    return {"name": name, "self_rating": rating}


def experience() -> dict[str, str]:
    return {
        "title": "校园二手交易平台",
        "start_date": "2024-03-01",
        "end_date": "2024-06-30",
        "description": "面向校内学生的二手交易 Web 应用",
        "responsibility": "负责后端 API 与数据库设计",
    }


def payload() -> dict[str, object]:
    return {
        "title": "我的第一份简历",
        "profile": profile(),
        "skills": [skill("Python", 4), skill("SQL"), skill("React")],
        "experiences": [experience()],
    }


def test_generate_resume_returns_html_and_saves_to_fake_repository() -> None:
    repository = get_fake_resume_repository()
    repository.clear()

    response = client.post("/api/v1/resumes/generate", json=payload())

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["resume_id"]
    assert data["title"] == "我的第一份简历"
    assert data["created_at"]
    assert "张三" in data["content_html"]
    assert "计算机科学与技术" in data["content_html"]
    assert "Python" in data["content_html"]
    assert "校园二手交易平台" in data["content_html"]
    assert repository.get(data["resume_id"]) == data


def test_generate_rejects_skill_count_outside_three_to_eight() -> None:
    too_few = payload()
    too_few["skills"] = [skill("Python"), skill("SQL")]
    too_many = payload()
    too_many["skills"] = [skill(str(index)) for index in range(9)]

    assert client.post("/api/v1/resumes/generate", json=too_few).status_code == 422
    assert client.post("/api/v1/resumes/generate", json=too_many).status_code == 422


def test_generate_rejects_experience_count_outside_one_to_three() -> None:
    none = payload()
    none["experiences"] = []
    too_many = payload()
    too_many["experiences"] = [experience() for _ in range(4)]

    assert client.post("/api/v1/resumes/generate", json=none).status_code == 422
    assert client.post("/api/v1/resumes/generate", json=too_many).status_code == 422


def test_generate_rejects_invalid_rating() -> None:
    for rating in (0, 6):
        invalid = payload()
        invalid["skills"] = [skill("Python", rating), skill("SQL"), skill("React")]

        assert client.post("/api/v1/resumes/generate", json=invalid).status_code == 422


def test_generate_rejects_invalid_date_range() -> None:
    invalid = payload()
    invalid["experiences"] = [experience() | {"end_date": "2024-02-28"}]

    assert client.post("/api/v1/resumes/generate", json=invalid).status_code == 422


def test_generate_rejects_duplicate_skill_names() -> None:
    invalid = payload()
    invalid["skills"] = [skill("Python"), skill("Python"), skill("React")]

    assert client.post("/api/v1/resumes/generate", json=invalid).status_code == 422


def test_generate_accepts_missing_end_date() -> None:
    valid = payload()
    del valid["experiences"][0]["end_date"]

    response = client.post("/api/v1/resumes/generate", json=valid)

    assert response.status_code == 200
    content_html = response.json()["data"]["content_html"]
    assert "至今" in content_html
    assert "2024-03-01" in content_html


def test_generate_rejects_missing_title() -> None:
    invalid = payload()
    del invalid["title"]

    response = client.post("/api/v1/resumes/generate", json=invalid)

    assert response.status_code == 422
    assert "title" in response.json()["detail"][0]["loc"]
