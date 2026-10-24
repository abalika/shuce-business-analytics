"""Publication gate verifies secrets inside files AND embedded archives."""
import importlib.util
import io
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('privacy_scan',ROOT/'scripts/privacy_scan.py')
scanner=importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


def test_private_reference_is_detected_without_echoing_value(tmp_path):
    token='synthetic-reference-value-'+('7'*32)
    env=tmp_path/'private.env'
    env.write_text('OPENAI_API_KEY='+token,encoding='utf-8')
    source=tmp_path/'public.txt'
    source.write_text('prefix '+token,encoding='utf-8')
    report=scanner.scan([source],[env])
    assert not report['ok'] and report['findings'][0]['rule']=='private_reference_match'
    assert token not in json.dumps(report)


def test_nested_xlsx_xml_cannot_hide_a_reference(tmp_path):
    token='synthetic-nested-value-'+('8'*32)
    env=tmp_path/'private.env'
    env.write_text('PROVIDER_TOKEN='+token,encoding='utf-8')
    inner=io.BytesIO()
    with zipfile.ZipFile(inner,'w') as z:z.writestr('xl/sharedStrings.xml',token)
    source=tmp_path/'delivery.zip'
    with zipfile.ZipFile(source,'w') as z:z.writestr('review.xlsx',inner.getvalue())
    report=scanner.scan([source],[env])
    assert not report['ok']
    assert any('!review.xlsx!xl/sharedStrings.xml' in item['path'] for item in report['findings'])
    assert token not in json.dumps(report)


def test_safe_simulation_and_private_path_policy(tmp_path):
    source=tmp_path/'simulation.csv'
    source.write_text('store,amount\nSIMULATED,2866\n',encoding='utf-8')
    assert scanner.scan([source])['ok']
    original=scanner.ROOT
    try:
        scanner.ROOT=tmp_path
        private=tmp_path/'data/customer.csv'
        private.parent.mkdir()
        private.write_text('synthetic',encoding='utf-8')
        report=scanner.scan([private])
        assert not report['ok'] and report['findings'][0]['rule']=='private_path'
    finally:scanner.ROOT=original
