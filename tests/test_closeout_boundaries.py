"""Input boundaries and no-write assertions for the final delivery contract."""
import io
import json
import pandas as pd
import pytest
from app import engine, main
from app.engine import AnalysisError, read_frame
from app.review_demo import demo_task
from tests.test_reviews import client, calculate


@pytest.mark.parametrize('extension', ['csv', 'xlsx'])
def test_row_limit_exact_and_plus_one(monkeypatch, extension):
    monkeypatch.setattr(engine, 'MAX_ROWS', 2)
    for count in (1, 2, 3):
        frame = pd.DataFrame({'a': range(count), 'b': ['NA'] * count})
        out = io.BytesIO()
        if extension == 'xlsx':
            frame.to_excel(out, index=False)
        else:
            out.write(frame.to_csv(index=False).encode())
        if count <= 2:
            assert len(read_frame(out.getvalue(), 'sample.' + extension)) == count
        else:
            with pytest.raises(AnalysisError, match='行数据'):
                read_frame(out.getvalue(), 'sample.' + extension)


@pytest.mark.parametrize('blob', [b'a,\n1,2\n', b' ,b\n1,2\n', b'a,a\n1,2\n', b'a, a \n1,2\n'])
def test_blank_or_duplicate_headers_are_actionable(blob):
    with pytest.raises(AnalysisError, match='列名'):
        read_frame(blob, 'bad.csv')


@pytest.mark.parametrize('name,blob', [('empty.csv', b''), ('header.csv', b'a,b\n'), ('empty.xlsx', b'broken'), ('bad.exe', b'data')])
def test_invalid_upload_never_registers_or_writes_artifacts(client, name, blob):
    before = set(main.DATA.iterdir())
    response = client.post('/api/ops/sources', files={'file': (name, blob)}, data={'kind': 'sales'})
    assert response.status_code == 422
    assert isinstance(response.json()['detail'], str)
    assert set(main.DATA.iterdir()) == before
    assert client.get('/api/ops/sources').json() == []


@pytest.mark.parametrize('period,accepted', [
    (('2024-01-01', '2024-12-31'), True),
    (('2024-01-01', '2025-01-01'), False),
    (('9999-12-31', '9999-12-31'), True),
    (('2026-09-27', '2026-09-21'), False),
    (('2026-02-29', '2026-03-01'), False),
])
def test_review_period_calendar_limits(period, accepted):
    task = {'start': period[0], 'end': period[1], 'previous_start': None, 'previous_end': None}
    if accepted:
        assert calculate(task=task)['period']['end'] == period[1]
    else:
        with pytest.raises(AnalysisError):
            calculate(task=task)


@pytest.mark.parametrize('query', ['offset=-1', 'limit=0', 'limit=101', 'offset=nope'])
def test_evidence_pagination_invalid_before_lookup(client, query):
    response = client.get('/api/ops/reviews/missing/evidence/E1?' + query)
    assert response.status_code == 422


@pytest.mark.parametrize('mapping', ['[]', 'null', '{"event": []}', 'not json'])
def test_mapping_errors_do_not_write_data(client, mapping):
    from app.review_demo import demo_blobs
    before = set(main.DATA.iterdir())
    response = client.post('/api/ops/sources', files={'file': ('s.csv', demo_blobs()['sales'])}, data={'kind': 'sales', 'mapping': mapping})
    assert response.status_code == 422
    assert set(main.DATA.iterdir()) == before
