from backend import utils
from model_fixtures import mock_llm
utils.call_llm = mock_llm
from backend.main import app
