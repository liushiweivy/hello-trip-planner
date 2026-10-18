# -*- coding: utf-8 -*-
"""评测集测试：大纲契约对黄金样本/反例/预制路线全量生效（零 token、离线）"""
import json
import os

import pytest

from app.services.seed_data import SEED_OUTLINES
from eval.outline_contract import ContractViolation, assert_outline_contract

_FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "eval", "fixtures")


def _load_fixture(name: str) -> dict:
    with open(os.path.join(_FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


# ---------------- 黄金样本 ----------------

def test_llm_sample_fixture_passes_contract():
    """LLM 大纲黄金样本（杭州 2 天）必须通过全部契约"""
    assert_outline_contract(_load_fixture("outline_sample_llm.json"),
                            travel_days=2, label="llm-sample")


# ---------------- 预制路线全量回归 ----------------

@pytest.mark.parametrize("city", sorted(SEED_OUTLINES.keys()))
def test_seed_outlines_pass_contract(city):
    """10 城预制路线全部满足契约（手工语料的回归护栏）"""
    assert_outline_contract({"plans": SEED_OUTLINES[city]},
                            travel_days=3, label=f"seed-{city}")


def test_seed_corpus_covers_expected_cities():
    """预制语料覆盖承诺过的热门城市清单"""
    expected = {"北京", "上海", "西安", "成都", "重庆", "杭州", "厦门", "广州", "南京", "青岛"}
    assert expected <= set(SEED_OUTLINES.keys())


# ---------------- 反例（评测器自检） ----------------

def test_bad_fixture_rejected_with_all_violation_types():
    """反例必须被拒，且第一处违规就是 plans 数量（契约校验器确实在干活）"""
    with pytest.raises(ContractViolation, match="恰好 4 套"):
        assert_outline_contract(_load_fixture("outline_bad_3plans.json"),
                                travel_days=1, label="bad")


@pytest.mark.parametrize("mutate,match", [
    (lambda p: p["plans"].__setitem__(0, {**p["plans"][0], "style": p["plans"][1]["style"]}),
     "风格必须互异"),
    (lambda p: p["plans"][0]["days"].pop(), "days 必须覆盖"),
    (lambda p: p["plans"][0]["days"][0].update(spots=[]), "景点数"),
    (lambda p: p["plans"][2].update(estimated_budget=99999), "经济实惠预算"),
    (lambda p: p["plans"][0]["days"][0]["spots"][0].update(
        location={"longitude": -74.0, "latitude": 40.7}), "坐标异常"),
])
def test_each_contract_rule_has_teeth(mutate, match):
    """逐条破坏黄金样本，对应契约条款必须报警（防断言形同虚设）"""
    payload = _load_fixture("outline_sample_llm.json")
    mutate(payload)
    with pytest.raises(ContractViolation, match=match):
        assert_outline_contract(payload, travel_days=2, label="mutated")
