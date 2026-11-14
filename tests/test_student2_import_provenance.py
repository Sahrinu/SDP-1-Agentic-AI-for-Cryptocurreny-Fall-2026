from pathlib import Path
import inspect
import sys

from student2.agents import mood_specialist, news_specialist, schemas


def test_specialists_and_schemas_are_loaded_from_the_team_repository():
    repository = Path(__file__).resolve().parents[1]
    original = repository.parent / "TradingAgents-main" / "TradingAgents-main"

    for module in (news_specialist, mood_specialist, schemas):
        assert Path(module.__file__).resolve().is_relative_to(repository)

    for implementation in (
        news_specialist.filter_articles_for_prediction,
        news_specialist.run_news_specialist,
        mood_specialist._compute_fng_summary,
        mood_specialist.run_mood_specialist,
        schemas.NewsSpecialistResult,
    ):
        assert Path(inspect.getfile(implementation)).resolve().is_relative_to(repository)

    assert original.resolve() not in {Path(path).resolve() for path in sys.path if path}
