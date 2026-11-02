from streamlit.testing.v1 import AppTest


def test_unconfigured_web_app_is_locked(tmp_path,monkeypatch):
    monkeypatch.delenv('OZON_PROFIT_LOCAL')
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py').run()
    assert not app.exception
    assert not app.sidebar.radio
    assert not (tmp_path/'profit.db').exists()
    assert any('Доступ закрыт' in m.value for m in app.info)


def test_web_login_required_before_database(tmp_path,monkeypatch):
    monkeypatch.delenv('OZON_PROFIT_LOCAL')
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py')
    app.secrets['app_password']='synthetic-owner-password-123'
    app.run()
    assert not app.exception
    assert not (tmp_path/'profit.db').exists()
    app.text_input[0].set_value('incorrect')
    app.button[0].click().run()
    assert app.error and not (tmp_path/'profit.db').exists()
    app.text_input[0].set_value('synthetic-owner-password-123')
    app.button[0].click().run()
    assert not app.exception
    assert app.sidebar.radio
    assert (tmp_path/'profit.db').exists()
    assert '_owner_password' not in app.session_state.filtered_state
    app.sidebar.button(key='owner_logout').click().run()
    assert not app.sidebar.radio
    assert '_owner_login' not in app.session_state.filtered_state


def test_short_web_password_does_not_unlock(tmp_path,monkeypatch):
    monkeypatch.delenv('OZON_PROFIT_LOCAL')
    monkeypatch.setenv('OZON_PROFIT_DATA_DIR',str(tmp_path))
    app=AppTest.from_file('app.py')
    app.secrets['app_password']='short'
    app.run()
    assert not app.exception
    assert not app.sidebar.radio
    assert not (tmp_path/'profit.db').exists()
