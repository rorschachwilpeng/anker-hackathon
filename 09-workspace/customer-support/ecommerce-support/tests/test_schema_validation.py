import pytest

def test_wrong_default_and_missing_on_update_rejected():
    from scripts.init_db import validate_column_defaults
    with pytest.raises(RuntimeError):validate_column_defaults('conversations','status',{'default':"'已结束'"})
    with pytest.raises(RuntimeError):validate_column_defaults('conversations','updated_at',{'default':'CURRENT_TIMESTAMP'})
    with pytest.raises(RuntimeError):validate_column_defaults('conversations','id',{'autoincrement':False})
    validate_column_defaults('conversations','updated_at',{'default':'CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP'})
