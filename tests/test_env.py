def test_environment_dependencies():
    import bs4
    import docx
    import neo4j
    import pydantic
    import streamlit
    assert bs4.__version__ is not None
    assert docx.__version__ is not None
    assert neo4j.__version__ is not None
    assert pydantic.__version__ is not None
    assert streamlit.__version__ is not None

