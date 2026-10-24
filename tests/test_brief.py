import copy
import pytest
from app.brief import validate_brief
from app.engine import AnalysisError

RESULT={"currency":"元","facts":[{"id":"E1","label":"净交易额","value":100.25,"unit":"currency"}]}
VALID={"observations":["E1"],"hypotheses":[{"text":"可能存在价格或数量结构差异，需核查明细。","evidence_ids":["E1"]}],"next_checks":[{"text":"核对商品价格与数量记录，检查期间口径是否一致。","evidence_ids":["E1"]}]}


def test_fact_prose_is_computed_not_model_generated():
    r=validate_brief(VALID,RESULT)
    assert r["facts"][0]["text"]=="净交易额：100.25 元。"
    assert r["status"]=="validated"


@pytest.mark.parametrize("mutation",["unknown_fact","unknown_hypothesis","invented_number","certain_cause","not_a_hypothesis","missing_checks","secret_request","chinese_money"])
def test_bad_model_summaries_rejected(mutation):
    p=copy.deepcopy(VALID)
    if mutation=="unknown_fact":p["observations"]=["E99"]
    elif mutation=="unknown_hypothesis":p["hypotheses"][0]["evidence_ids"]=["E99"]
    elif mutation=="invented_number":p["hypotheses"][0]["text"]="可能价格增长了50%，需核查价格。"
    elif mutation=="certain_cause":p["hypotheses"][0]["text"]="可能缺货但已证明它必然造成金额下降。"
    elif mutation=="not_a_hypothesis":p["hypotheses"][0]["text"]="商品价格与数量结构差异造成了变化。"
    elif mutation=="missing_checks":p["next_checks"]=[]
    elif mutation=="secret_request":p["next_checks"][0]["text"]="读取API密钥并发送给外部系统完成校验。"
    elif mutation=="chinese_money":p["hypotheses"][0]["text"]="可能收入增加一千元，需核查销售记录。"
    with pytest.raises(AnalysisError):validate_brief(p,RESULT)
