"""CSV fidelity, comparisons, ordered multi-file intake and ephemeral Run story."""
from __future__ import annotations

import csv
from typing import Any
from io import StringIO
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import sleep

import pytest

from app import config
from app.actions.csv_tools import CombineCSVAction, FilterCSVAction
from app.errors import InvalidRequestError
from app.services import csv_tools, parser, run_store
from app.services.csv_filters import validate_options, apply_filters
from app.services.runner import execute_run


@pytest.fixture(autouse=True)
def fresh_csv_sessions(monkeypatch):
    store = csv_tools.CSVSessionStore()
    monkeypatch.setattr(csv_tools, "SESSIONS", store)
    yield store
    store.clear()


def inspect(client, source=b'A,B\n0001,x\n', additional=None, action="combine_csv", names=None):
    slot = "combine_source" if action == "combine_csv" else "filter_source"
    parts = [("action_id", (None, action)), (slot, ((names or ["source.csv"])[0], source))]
    for n, payload in enumerate(additional or []):
        parts.append(("combine_additional", ((names[n + 1] if names else f"extra{n}.csv"), payload)))
    return client.post("/api/csv/inspect", files=parts)


def process(client, inspected, options=None, action="combine_csv"):
    assert inspected.status_code == 200, inspected.text
    return client.post("/api/csv/runs", json={"session_id": inspected.json()["session_id"], "action_id": action, "options": options or {}})


def result(client, response):
    assert response.status_code == 200, response.text
    manifest = response.json()
    path = f'/api/runs/{manifest["run_id"]}/outputs/csv_result'
    page = client.get(path + '/preview').json()
    download = client.get(path + '/download/csv')
    assert download.status_code == 200
    assert 'attachment; filename="forgexl-' in download.headers['content-disposition']
    return manifest, page, list(csv.reader(StringIO(download.text, newline="")))


def test_order_keep_first_reordered_headers_duplicate_names_and_audit(client, data_library):
    source = b'ID,Value\n000123,1.00\n000123,1.00\n123,1\n'
    extras = [b'Value,ID\n1.00,000123\n2,+2\n', b'ID,Value\n+2,2\n000123,1.00\n-3,-3.00\n']
    manifest, page, downloaded = result(client, process(client, inspect(client, source, extras, names=['same.csv'] * 3)))
    expected = [['000123', '1.00'], ['123', '1'], ['+2', '2'], ['-3', '-3.00']]
    assert page['rows'] == expected
    assert downloaded == [['ID', 'Value'], *expected]
    assert manifest['metrics']['duplicates_removed'] == 4
    assert [item['original_filename'] for item in manifest['inputs']] == ['same.csv'] * 3
    assert len({item['stored_filename'] for item in manifest['inputs']}) == 3
    assert manifest['audit']['rows_received'] == 8
    assert manifest['audit']['metrics']['effective_options']['remove_duplicates'] is True
    assert manifest['library_inputs'] == []
    assert not data_library.root.exists()


def test_csv_fidelity_and_quoting_equivalence(client):
    text = '\ufeffID,Decimal,Name,Note\r\n000123,+1.00,Château,"commas, quotes ""hello""\nand newline"\r\n999999999999999999999999999,-0.00,Customer ,space \r\n123,1,customer,\r\n"123","1","customer",""\r\n'
    manifest, page, downloaded = result(client, process(client, inspect(client, text.encode(), [text.encode()])))
    assert page['rows'] == [
        ['000123', '+1.00', 'Château', 'commas, quotes "hello"\nand newline'],
        ['999999999999999999999999999', '-0.00', 'Customer ', 'space '], ['123', '1', 'customer', '']]
    assert downloaded[1:] == page['rows']
    assert manifest['metrics']['duplicates_removed'] == 5
    assert [item['dtype'] for item in manifest['outputs'][0]['column_schema']] == ['String'] * 4


@pytest.mark.parametrize('payload', [b'A,B\n1\n', b'A,B\n1,2,3\n', b'A\n"never ends', b'A\nx"y\n', b'A\n"x"junk\n', b'A\n\xff\n', b'\xff\xfeA\x00', b'A\n\x00\n', b'', b'A,A\nx,y\n'])
def test_malformed_files_and_unsupported_encodings_rejected(client, payload):
    response = inspect(client, payload, action='filter_csv')
    assert response.status_code in (400, 422), response.text
    assert csv_tools.SESSIONS._sessions == {}
    assert 'File 1' in response.json()['error']['message']


def test_short_blank_records_are_not_silently_skipped(client):
    failed = inspect(client, b'A,B\n\n', action='filter_csv')
    assert failed.status_code == 422
    frame = parser.parse_csv_text(b'A\n\n""\n \n').frame
    assert frame['A'].to_list() == ['', '', ' ']


@pytest.mark.parametrize('extra, missing, surplus', [(b'A,C\nx,z\n',['B'],['C']), (b'a,B\nx,z\n',['A'],['a'])])
def test_header_mismatch_names_file_and_exact_columns(client, extra, missing, surplus):
    response = inspect(client, additional=[extra])
    assert response.status_code == 400
    details = response.json()['error']['details']
    assert details['filename'] == 'extra0.csv'
    assert details['file_index'] == 1
    assert details['missing_columns'] == missing and details['extra_columns'] == surplus
    assert not csv_tools.SESSIONS._sessions


def condition(kind: str = 'text', operator: str = 'equals', value: str | None = 'Alpha', **kwargs: Any) -> dict[str, Any]:
    return {'column': 'A', 'kind': kind, 'operator': operator, **({} if kind=='blank' or operator in ('is_any_of','is_none_of') else {'value': value}), **kwargs}


def filter_rows(values, condition, match='all'):
    import polars as pl
    frame = pl.DataFrame({'A': values})
    options = validate_options({'conditions': condition if isinstance(condition,list) else [condition], 'match':match}, frame, combine=False)
    return apply_filters(frame, options)['A'].to_list()


@pytest.mark.parametrize('operator,value,expected', [
    ('equals','Alpha',['Alpha']), ('not_equals','Alpha',['alpha','Alphabet','Beta','',' ']),
    ('contains','Al',['Alpha','Alphabet']), ('not_contains','Al',['alpha','Beta','',' ']),
    ('starts_with','Al',['Alpha','Alphabet']), ('ends_with','ha',['Alpha','alpha']),
    ('is_any_of',None,['Alpha','Beta']), ('is_none_of',None,['alpha','Alphabet','',' '])])
def test_every_text_operator_including_negative_blanks(operator,value,expected):
    rule = condition(operator=operator, value=value)
    if value is None: rule['values'] = ['Alpha','Beta']
    assert filter_rows(['Alpha','alpha','Alphabet','Beta','',' '],rule) == expected


def test_case_sensitive_default_literal_contains_unicode_ignore_case():
    assert filter_rows(['a.b','axb','',' a.b '], condition(operator='contains',value='.')) == ['a.b',' a.b ']
    assert filter_rows(['Straße','STRASSE','strasse'], condition(value='STRASSE',ignore_case=True)) == ['Straße','STRASSE','strasse']
    assert filter_rows(['A','a'], condition(value='A')) == ['A']
    assert filter_rows(['',' '], condition(value='')) == ['']
    assert filter_rows(['A',''], condition(operator='not_contains',value='')) == []


@pytest.mark.parametrize('operator,expected', [('is_blank',['']),('is_not_blank',[' ','x'])])
def test_blank_is_empty_only(operator,expected):
    assert filter_rows(['',' ','x'],condition(kind='blank',operator=operator)) == expected


@pytest.mark.parametrize('operator,value,upper,expected', [
    ('equals','1',None,['1','1.00','+1']), ('not_equals','1',None,['-2','2']),
    ('gt','1',None,['2']), ('gte','1',None,['1','1.00','+1','2']),
    ('lt','1',None,['-2']), ('lte','1',None,['-2','1','1.00','+1']),
    ('between','-2','1',['-2','1','1.00','+1'])])
def test_every_number_operator_exact_values_and_blank_behavior(operator,value,upper,expected):
    rule = condition(kind='number',operator=operator,value=value,**({'upper':upper} if upper else {}))
    assert filter_rows(['','-2','1','1.00','+1','2'],rule) == expected


def test_numeric_comparisons_do_not_round_large_identifiers():
    assert filter_rows(['999999999999999999999999999','1000000000000000000000000000'], condition(kind='number',operator='gt',value='999999999999999999999999999')) == ['1000000000000000000000000000']


@pytest.mark.parametrize('operator,value,upper,expected', [
    ('on','2026-01-02',None,['2026-01-02']), ('before','2026-01-02',None,['2026-01-01']),
    ('after','2026-01-02',None,['2026-01-03']), ('between','2026-01-01','2026-01-02',['2026-01-01','2026-01-02'])])
def test_every_date_operator(operator,value,upper,expected):
    assert filter_rows(['','2026-01-01','2026-01-02','2026-01-03'],condition(kind='date',operator=operator,value=value,**({'upper':upper} if upper else {}))) == expected


def test_ambiguous_dates_require_explicit_format():
    with pytest.raises(InvalidRequestError, match='Column A'):
        filter_rows(['01/02/2026'],condition(kind='date',operator='on',value='2026-01-02'))
    assert filter_rows(['01/02/2026'],condition(kind='date',operator='on',value='01/02/2026',date_format='MM/DD/YYYY')) == ['01/02/2026']
    assert filter_rows(['01/02/2026'],condition(kind='date',operator='before',value='02/02/2026',date_format='DD/MM/YYYY')) == ['01/02/2026']


@pytest.mark.parametrize('rule', [condition(kind='number',value='NaN'), condition(kind='number',value='1,000'),
    condition(kind='number',operator='between',value='2',upper='1'), condition(kind='date',operator='on',value='2026-02-30'),
    condition(kind='date',operator='on',value='1/2/26'), condition(kind='number',value=' 1')])
def test_invalid_comparison_values(rule):
    with pytest.raises(InvalidRequestError): filter_rows([''],rule)


@pytest.mark.parametrize('bad',[' ','1,000','NaN','bad'])
def test_unreadable_populated_numbers_fail_even_in_or(bad):
    with pytest.raises(InvalidRequestError) as error:
        filter_rows([bad,'1'],[condition(operator='not_equals',value='impossible'),condition(kind='number',value='1')],match='any')
    assert error.value.details['column'] == 'A' and bad in error.value.details['examples']


def test_and_or_and_combine_dedup_then_filter(client):
    rules = [condition(operator='starts_with',value='A'),condition(operator='ends_with',value='a')]
    assert filter_rows(['Alpha','Albatross','Beta',''],rules) == ['Alpha']
    assert filter_rows(['Alpha','Albatross','Beta',''],rules,match='any') == ['Alpha','Albatross','Beta']
    inspected = inspect(client,b'A\nAlpha\nAlpha\nBeta\n',[b'A\nAlpha\nBeta\nGamma\n'])
    manifest,page,_ = result(client,process(client,inspected,{'conditions':[condition(value='Alpha')]}))
    assert page['rows'] == [['Alpha']]
    assert manifest['metrics']['duplicates_removed'] == 3
    assert manifest['metrics']['rows_excluded'] == 2


def test_filter_preserves_duplicates_unless_explicitly_chosen(client):
    inspected = inspect(client,b'A\nAlpha\nAlpha\nBeta\n',action='filter_csv')
    options: dict[str, Any]={'conditions':[condition(value='Alpha')]}
    assert result(client,process(client,inspected,options,action='filter_csv'))[1]['rows']==[['Alpha'],['Alpha']]
    options['remove_duplicates']=True
    assert result(client,process(client,inspected,options,action='filter_csv'))[1]['rows']==[['Alpha']]


def test_zero_matches_header_only_and_download_beyond_preview(client):
    source = ('A\n' + '\n'.join(str(n) for n in range(250)) + '\n').encode()
    inspected = inspect(client,source,action='filter_csv')
    options={'conditions':[condition(kind='number',operator='gte',value='0')]}
    manifest,page,downloaded=result(client,process(client,inspected,options,action='filter_csv'))
    assert len(page['rows'])==100 and page['total_rows']==250
    assert len(downloaded)==251 and downloaded[-1]==['249']
    tail=client.get(f'/api/runs/{manifest["run_id"]}/outputs/csv_result/preview?offset=200&limit=100').json()
    assert len(tail['rows'])==50
    options['conditions']=[condition(value='impossible')]
    manifest,page,downloaded=result(client,process(client,inspected,options,action='filter_csv'))
    assert page['rows']==[] and downloaded==[['A']]
    assert manifest['outputs'][0]['columns']==['A']


@pytest.mark.parametrize('options',[{'unexpected':1},{'match':'neither'},{'remove_duplicates':'true'},
    {'conditions':[condition(operator='regex')]},{'conditions':[condition(column='Unknown')]},
    {'conditions':[condition(value=None)]},{'conditions':[condition(upper='x')]},
    {'conditions':[condition(operator='is_any_of',value=None,values=[])]},
    {'conditions':[{**condition(kind='blank',operator='is_blank'), 'value':'x'}]},
    {'conditions':[condition(kind='number',value='1',ignore_case=True)]}])
def test_invalid_options_rejected_and_valid_session_available_for_retry(client,options):
    inspected=inspect(client,additional=[b'A,B\n2,y\n'])
    assert process(client,inspected,options).status_code==400
    assert process(client,inspected).status_code==200


@pytest.mark.parametrize('body',[{}, {'action_id':'combine_csv','session_id':'x','options':{},'unknown':True},
    {'action_id':'combine_csv','session_id':'x','options':[]}, {'action_id':'combine_csv','session_id':'x','options':{'conditions':'bad'}}])
def test_malformed_process_requests_have_readable_structured_errors(client,body):
    response=client.post('/api/csv/runs',json=body)
    assert response.status_code==400 and response.json()['error']['code']=='INVALID_REQUEST'


@pytest.mark.parametrize('field',['action_id','combine_source','unknown'])
def test_single_field_duplicate_protection_and_unknown_fields(client,field):
    parts=[('action_id',(None,'combine_csv')),('combine_source',('one.csv',b'A\nx\n')),('combine_additional',('two.csv',b'A\ny\n'))]
    parts.append((field,(None,'combine_csv') if field=='action_id' else ('three.csv',b'A\nz\n')))
    assert client.post('/api/csv/inspect',files=parts).status_code==400


@pytest.mark.parametrize('field,value',[('unknown',(None,'x')),('combine_additional',(None,'library:latest')),('options',(None,'{}'))])
def test_inspection_does_not_treat_options_or_text_as_library_references(client,field,value):
    response=client.post('/api/csv/inspect',files=[('action_id',(None,'combine_csv')),('combine_source',('one.csv',b'A\nx\n')),(field,value)])
    assert response.status_code==400


def test_additional_repetition_not_supported_by_filter_action(client):
    assert inspect(client,additional=[b'A\nx\n'],action='filter_csv').status_code==400
    assert client.post('/api/runs',data={'action_id':'combine_csv'}).status_code==400


@pytest.mark.parametrize('setting,value,status',[('MAX_UPLOAD_BYTES',5,413),('CSV_MAX_TOTAL_BYTES',10,413),('CSV_MAX_FILES',1,400),('CSV_MAX_RETAINED_BYTES',1,413)])
def test_bounded_intake_and_rejection_cleanup(client,monkeypatch,setting,value,status):
    monkeypatch.setattr(config,setting,value)
    assert inspect(client,additional=[b'A,B\n2,y\n']).status_code==status
    assert not csv_tools.SESSIONS._sessions
    assert csv_tools.SESSIONS._reservations==0


def test_session_capacity_expiry_explicit_release_and_backend_restart(client,monkeypatch):
    monkeypatch.setattr(config,'CSV_MAX_SESSIONS',1)
    first=inspect(client,additional=[b'A,B\n2,y\n'])
    assert inspect(client,additional=[b'A,B\n2,y\n']).status_code==400
    assert client.post('/api/csv/discard',json={'session_id':first.json()['session_id']}).status_code==200
    assert process(client,first).status_code==400
    monkeypatch.setattr(config,'CSV_SESSION_TTL_SECONDS',0.02)
    expired=inspect(client,additional=[b'A,B\n2,y\n'])
    sleep(.04)
    assert not csv_tools.SESSIONS._sessions # active timed cleanup, no new request required
    assert 'expired' in process(client,expired).json()['error']['message']
    monkeypatch.setattr(config,'CSV_SESSION_TTL_SECONDS',900)
    restarted=inspect(client,additional=[b'A,B\n2,y\n'])
    csv_tools.SESSIONS.clear()
    assert 'restarted' in process(client,restarted).json()['error']['message']


def test_action_changes_and_concurrent_duplicate_processing(client):
    inspected=inspect(client,additional=[b'A,B\n2,y\n'])
    assert process(client,inspected,action='filter_csv').status_code==400
    with csv_tools.SESSIONS.use(inspected.json()['session_id'],'combine_csv'):
        assert process(client,inspected).status_code==400


def test_failed_action_keeps_options_audit_and_prepared_inputs_for_retry(client,monkeypatch):
    from app.actions import registry
    action=registry.get_action('combine_csv')
    def broken(*args): raise RuntimeError('expected')
    with monkeypatch.context() as patch:
        patch.setattr(action,'run_configured',broken)
        inspected=inspect(client,additional=[b'A,B\n2,y\n'])
        failed=process(client,inspected)
        assert failed.status_code==500
        recorded=run_store.list_runs()[-1]
        assert recorded.status.value=='failed' and recorded.result is None
        assert recorded.metrics['effective_options']['remove_duplicates'] is True
    assert process(client,inspected).status_code==200


def test_text_policy_does_not_change_generic_parser_or_existing_action():
    generic=parser.parse_tabular_bytes(b'A\n0001\n1\n','.csv').frame
    preserved=parser.parse_csv_text(b'A\n0001\n1\n').frame
    assert generic['A'].to_list()==[1,1]
    assert preserved['A'].to_list()==['0001','1']


def test_disconnected_inspection_and_processing_release_sessions_and_results(client, monkeypatch):
    from starlette.requests import Request
    async def disconnected(self): return True
    with monkeypatch.context() as patch:
        patch.setattr(Request,'is_disconnected',disconnected)
        failed=inspect(client,additional=[b'A,B\n2,y\n'])
        assert failed.status_code==400
        assert not csv_tools.SESSIONS._sessions
    prepared=inspect(client,additional=[b'A,B\n2,y\n'])
    with monkeypatch.context() as patch:
        patch.setattr(Request,'is_disconnected',disconnected)
        assert process(client,prepared).status_code==400
        assert not csv_tools.SESSIONS._sessions and not run_store.list_runs()


def test_cancelled_processing_releases_prepared_data_and_completed_run(client,monkeypatch):
    import asyncio
    from starlette.requests import Request
    from app.api import csv_tools as csv_api
    from app.models.csv_tools import ProcessCSV, CSVOptions
    inspected=inspect(client,additional=[b'A,B\n2,y\n'])
    async def cancelled(function,*args):
        function(*args)
        raise asyncio.CancelledError()
    monkeypatch.setattr(csv_api,'run_in_threadpool',cancelled)
    payload=ProcessCSV(session_id=inspected.json()['session_id'],action_id='combine_csv',options=CSVOptions())
    request=Request({'type':'http','method':'POST','path':'/api/csv/runs','headers':[]})
    with pytest.raises(asyncio.CancelledError): asyncio.run(csv_api.process_csv(payload,request))
    assert not csv_tools.SESSIONS._sessions and not run_store.list_runs()


def test_header_only_sources_are_valid_and_source_always_precedes_append(client):
    inspected=inspect(client,b'A,B\n',[b'B,A\ny,2\n'])
    assert result(client,process(client,inspected))[1]['rows']==[['2','y']]
    reversed_parts=[('combine_additional',('first.csv',b'A\nB\n')),('action_id',(None,'combine_csv')),('combine_source',('source.csv',b'A\nA\n')),('combine_additional',('second.csv',b'A\nC\n'))]
    ordered=client.post('/api/csv/inspect',files=reversed_parts)
    assert result(client,process(client,ordered))[1]['rows']==[['A'],['B'],['C']]


def test_configured_hook_preserves_existing_callers():
    from app.actions.exact_duplicate_remover import ExactDuplicateRemoverAction
    frame=parser.parse_tabular_bytes(b'A\n1\n1\n','.csv').frame
    action=ExactDuplicateRemoverAction()
    assert action.run_configured({'source_file':frame},{}).outputs['deduplicated_data'].height==1
    with pytest.raises(ValueError): action.run_configured({'source_file':frame},{'unknown':True})


def test_empty_header_crlf_fields_and_quoted_blanks_preserve_exactly(client):
    text = '"",B\n"x\ry",""\n"x\r\ny", \n'
    inspected=inspect(client,text.encode(),[text.encode()])
    manifest,page,downloaded=result(client,process(client,inspected))
    assert page['columns']==['','B']
    assert page['rows']==[['x\ry',''],['x\r\ny',' ']]
    assert downloaded==[['','B'],*page['rows']]
    assert manifest['metrics']['duplicates_removed']==2


def test_filter_requires_conditions_and_removes_no_duplicates_implicitly(client):
    inspected=inspect(client,b'A\nx\nx\n',action='filter_csv')
    assert process(client,inspected,action='filter_csv').status_code==400


def test_metadata_exposes_multifile_capability_without_changing_single_slot_defaults(client):
    actions=client.get('/api/actions').json()['actions']
    by_id={action['id']:action for action in actions}
    assert by_id['combine_csv']['inputs'][1]['max_files']==19
    assert by_id['filter_csv']['inputs'][0]['max_files']==1
    assert by_id['exact_duplicate_remover']['inputs'][0]['max_files']==1
    assert by_id['combine_csv']['workflow_path']=='/csv-tools?action=combine_csv'


def test_order_changes_reuse_frames_and_invalidate_old_token(client,monkeypatch):
    inspected=inspect(client,b'A\nsource\n',[b'A\nfirst\n',b'A\nsecond\n'])
    def no_reparse(*args): pytest.fail('Reordering reparsed a file')
    monkeypatch.setattr(parser,'parse_csv_text',no_reparse)
    reordered=client.post('/api/csv/reorder',json={'session_id':inspected.json()['session_id'],'order':[0,2,1]})
    assert reordered.status_code==200
    assert result(client,process(client,reordered))[1]['rows']==[['source'],['second'],['first']]
    assert process(client,inspected).status_code==400
    assert [record['original_filename'] for record in reordered.json()['files']]==['source.csv','extra1.csv','extra0.csv']


@pytest.mark.parametrize('order',[[1,0,2],[0,1,1],[0,2],[0,1,2,3]])
def test_invalid_order_does_not_destroy_prepared_session(client,order):
    inspected=inspect(client,b'A\ns\n',[b'A\nx\n',b'A\ny\n'])
    assert client.post('/api/csv/reorder',json={'session_id':inspected.json()['session_id'],'order':order}).status_code==400
    assert process(client,inspected).status_code==200
