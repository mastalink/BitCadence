"""Desktop interaction regressions; no real processes, Tk roots or task writes."""
from types import SimpleNamespace
from unittest.mock import Mock

from mco.desktop.app import DesktopApp
from mco.desktop.controller import DesktopController
from mco.fleet import WorkerConfig


def config(name, mode='waker'):
    return WorkerConfig(name,name,name,mode,None,10,30)


def controller(tmp_path):
    value = DesktopController.__new__(DesktopController)
    value.fleet_path = tmp_path/'fleet.toml'
    value.supervisor = SimpleNamespace(workers={
        'gateway':SimpleNamespace(config=config('gateway'),process=None,state=SimpleNamespace(value='running')),
        'example':SimpleNamespace(config=config('example'),process=None,state=SimpleNamespace(value='idle'))})
    value.active_tasks = Mock(return_value=['BitCadence-example'])
    value.external = Mock(return_value=[])
    value.stop = Mock(side_effect=AssertionError('preview must not stop'))
    value.start = Mock(side_effect=AssertionError('preview must not start'))
    return value


def test_handoff_preview_reads_scope_without_tick_or_mutation(tmp_path):
    value = controller(tmp_path)
    result = value.preview_handoff()
    assert result['tasks'] == ['BitCadence-example']
    assert [row['name'] for row in result['components']] == ['gateway','example']
    value.stop.assert_not_called()
    value.start.assert_not_called()


def test_reload_preview_identifies_removed_changed_and_added(tmp_path,monkeypatch):
    from mco.desktop import controller as module
    value = controller(tmp_path)
    value.fleet_path.write_text('# fixture',encoding='utf-8')
    monkeypatch.setattr(module,'load_fleet',lambda p:{'new':config('new')})
    result=value.preview_reload()
    assert result['removed']==['example']
    assert result['added']==['new','scheduler']
    value.stop.assert_not_called()


def bare_app():
    value=DesktopApp.__new__(DesktopApp)
    value.root=object()
    value.busy=False
    value.closing=False
    value.submit=Mock()
    value.controller=SimpleNamespace(preview_handoff=Mock(return_value={'tasks':['task-one'],'components':[{'name':'worker','state':'running','pids':['42']}]}),
                                     take_control=Mock(),preview_reload=Mock(return_value={'removed':['old'],'changed':['worker'],'added':['new']}),reload=Mock())
    return value


def test_declined_handoff_never_submits_process_action(monkeypatch):
    from mco.desktop import app as module
    value=bare_app()
    confirm=Mock(return_value=False)
    monkeypatch.setattr(module.messagebox,'askokcancel',confirm)
    value.review_handoff()
    value.submit.assert_not_called()
    message=confirm.call_args.args[1]
    assert 'task-one' in message and 'worker' in message and '42' in message


def test_confirmed_handoff_submits_existing_take_control(monkeypatch):
    from mco.desktop import app as module
    value=bare_app()
    monkeypatch.setattr(module.messagebox,'askokcancel',lambda *a,**k:True)
    value.review_handoff()
    value.submit.assert_called_once_with(value.controller.take_control)


def test_apply_fleet_changes_preview_cancel_and_accept(monkeypatch):
    from mco.desktop import app as module
    value=bare_app()
    confirm=Mock(return_value=False)
    monkeypatch.setattr(module.messagebox,'askokcancel',confirm)
    value.review_reload()
    value.submit.assert_not_called()
    assert all(word in confirm.call_args.args[1] for word in ['old','worker','new'])
    confirm.return_value=True
    value.review_reload()
    value.submit.assert_called_once_with(value.controller.reload)


def test_selected_buttons_disabled_without_selection_and_while_busy():
    value=bare_app()
    value.table=SimpleNamespace(selection=lambda:())
    value.selected_buttons=[Mock(),Mock()]
    value.update_selection_actions()
    assert all(button.state.call_args.args[0]==['disabled'] for button in value.selected_buttons)
    value.table.selection=lambda:('worker',)
    value.update_selection_actions()
    assert all(button.state.call_args.args[0]==['!disabled'] for button in value.selected_buttons)
    value.busy=True
    value.update_selection_actions()
    assert all(button.state.call_args.args[0]==['disabled'] for button in value.selected_buttons)
