from datetime import datetime

import pytest


@pytest.fixture
def db_session(app):
    session = app.db_session()  # type: ignore[attr-defined]
    try:
        yield session
    finally:
        session.close()


def test_dynamic_profile_responses(db_session):
    from app.services.chatbot_profile import generate_dynamic_response
    from app.models.profile import Profile
    from app.models.project import Project

    db_session.query(Profile).delete()
    db_session.query(Project).delete()

    profile = Profile(
        full_name="Osmar Silva",
        role="Desenvolvedor Freelancer",
        specialization="soluções web, mobile e integrações personalizadas",
        education="Formação em Desenvolvimento de Software e Computação em Nuvem",
        current_studies="Android, Python, IoT e Inteligência Artificial",
        availability="Disponível para novos projetos",
        website="https://osmardev.online",
    )
    project = Project(
        company_id=1,
        name="IPTV",
        description="Plataforma IPTV com painel administrativo e aplicativos multi-dispositivo.",
        status="Em produção",
        github_url="https://github.com/osmardesenvolvedordesoftware/iptv",
        created_at=datetime(2024, 5, 20),
    )

    db_session.add(profile)
    db_session.add(project)
    db_session.commit()

    msg1 = "quem é o desenvolvedor?"
    response1 = generate_dynamic_response(db_session, msg1)
    assert "O desenvolvedor é" in response1

    msg1b = "me fala do desenvolvedor"
    response1b = generate_dynamic_response(db_session, msg1b)
    assert "O desenvolvedor é" in response1b

    msg2 = "me fala do projeto IPTV"
    response2 = generate_dynamic_response(db_session, msg2)
    assert "projeto **IPTV**" in response2

    msg3 = "quais projetos você tem?"
    response3 = generate_dynamic_response(db_session, msg3)
    assert "trabalhou nos seguintes projetos" in response3
