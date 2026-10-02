"""テストで共通に使う準備(フィクスチャ:テスト用の下ごしらえ)。

生成には少し時間がかかるので、同じ結果を複数のテストで使い回す(scope="session")。
統計の確認は、シードを固定した多数回の生成で行い、結果が毎回同じになるようにしている。
"""

import pytest

from pennant import generate_draft_class, generate_league, load_generation_config, load_name_parts

LEAGUE_SEEDS = range(1, 11)  # 10 回分のリーグ(840 人 × 10)


@pytest.fixture(scope="session")
def config():
    return load_generation_config()


@pytest.fixture(scope="session")
def names():
    return load_name_parts()


@pytest.fixture(scope="session")
def league(config, names):
    return generate_league(2024, config, names)


@pytest.fixture(scope="session")
def leagues(config, names):
    return [generate_league(seed, config, names) for seed in LEAGUE_SEEDS]


@pytest.fixture(scope="session")
def all_players(leagues):
    return [p for lg in leagues for p in lg.all_players()]


@pytest.fixture(scope="session")
def draft_class(config, names):
    return generate_draft_class(7, 4000, config, names)
