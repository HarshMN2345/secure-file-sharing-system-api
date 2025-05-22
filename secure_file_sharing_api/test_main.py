import pytest
import pytest_asyncio
import asyncio
from fastapi.testclient import TestClient
from main import app, get_password_hash
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime
import os
import shutil
from contextlib import asynccontextmanager
from httpx import AsyncClient

# Mock MongoDB for tests
@pytest_asyncio.fixture(scope="session")
async def setup_test_db():
    # Use in-memory MongoDB or a test database
    # In a real scenario, you might want to use a separate test database
    # Here we're just using a mock connection
    app.state.client = AsyncIOMotorClient("mongodb://localhost:27017")
    app.state.db = app.state.client.test_db
    yield
    app.state.client.close()

@pytest_asyncio.fixture(scope="function")
async def clear_db(setup_test_db):
    # Clear test collections before each test
    collections = await app.state.db.list_collection_names()
    for collection in collections:
        await app.state.db.drop_collection(collection)
    yield

@pytest_asyncio.fixture
async def async_client(setup_test_db, clear_db):
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

# Test data
TEST_USER = {
    "email": "test@example.com",
    "password": "testpassword123",
    "role": "ops"
}

TEST_CLIENT = {
    "email": "client@example.com",
    "password": "clientpass123",
    "role": "client"
}

@pytest.fixture(autouse=True)
def setup_teardown():
    # Setup: Create test upload directory
    os.makedirs("./uploads", exist_ok=True)
    yield
    # Teardown: Clean up test upload directory
    shutil.rmtree("./uploads", ignore_errors=True)

# Update main.py to use app.state for database access in tests
# This monkeypatching is for testing purposes
from main import db as original_db
@pytest.fixture(autouse=True)
def patch_db(monkeypatch):
    # This will make main.py functions use our test database instead
    def get_test_db():
        if hasattr(app.state, 'db'):
            return app.state.db
        return original_db
    monkeypatch.setattr("main.db", get_test_db())

@pytest.mark.asyncio
async def test_signup(async_client):
    response = await async_client.post("/signup", json=TEST_USER)
    assert response.status_code == 200
    assert "secure_url" in response.json()
    assert "message" in response.json()

@pytest.mark.asyncio
async def test_signup_duplicate_email(async_client):
    # First signup
    await async_client.post("/signup", json=TEST_USER)
    # Try to signup again with same email
    response = await async_client.post("/signup", json=TEST_USER)
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

@pytest.mark.asyncio
async def test_login_before_verification(async_client):
    # Signup but don't verify
    await async_client.post("/signup", json=TEST_USER)
    response = await async_client.post("/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]})
    assert response.status_code == 403
    assert response.json()["detail"] == "Email not verified"

@pytest.mark.asyncio
async def test_verify_email(async_client):
    # Signup
    signup_response = await async_client.post("/signup", json=TEST_USER)
    verify_url = signup_response.json()["secure_url"]
    # Extract token from URL
    token = verify_url.split("/")[-1]
    # Verify email
    response = await async_client.get(f"/verify-email/{token}")
    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"

@pytest.mark.asyncio
async def test_login_after_verification(async_client):
    # Signup and verify
    signup_response = await async_client.post("/signup", json=TEST_USER)
    verify_url = signup_response.json()["secure_url"]
    token = verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{token}")
    
    # Try to login
    response = await async_client.post("/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]})
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "token_type" in response.json()

@pytest.mark.asyncio
async def test_upload_file(async_client):
    # Signup, verify and login as ops user
    signup_response = await async_client.post("/signup", json=TEST_USER)
    verify_url = signup_response.json()["secure_url"]
    token = verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{token}")
    login_response = await async_client.post("/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]})
    access_token = login_response.json()["access_token"]
    
    # Create a test file
    test_file_content = b"test content"
    files = {"file": ("test.pptx", test_file_content, "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = await async_client.post("/upload", files=files, headers=headers)
    assert response.status_code == 200
    assert "file_id" in response.json()

@pytest.mark.asyncio
async def test_upload_invalid_file_type(async_client):
    # Signup, verify and login as ops user
    signup_response = await async_client.post("/signup", json=TEST_USER)
    verify_url = signup_response.json()["secure_url"]
    token = verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{token}")
    login_response = await async_client.post("/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]})
    access_token = login_response.json()["access_token"]
    
    # Try to upload invalid file type
    test_file_content = b"test content"
    files = {"file": ("test.txt", test_file_content, "text/plain")}
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = await async_client.post("/upload", files=files, headers=headers)
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid file type"

@pytest.mark.asyncio
async def test_list_files(async_client):
    # Signup and verify client user
    signup_response = await async_client.post("/signup", json=TEST_CLIENT)
    verify_url = signup_response.json()["secure_url"]
    token = verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{token}")
    
    # Login as client
    login_response = await async_client.post("/login", data={"username": TEST_CLIENT["email"], "password": TEST_CLIENT["password"]})
    access_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {access_token}"}
    
    response = await async_client.get("/files", headers=headers)
    assert response.status_code == 200
    assert isinstance(response.json(), list)

@pytest.mark.asyncio
async def test_download_file(async_client):
    # Setup: Upload a file as ops user
    signup_response = await async_client.post("/signup", json=TEST_USER)
    verify_url = signup_response.json()["secure_url"]
    token = verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{token}")
    login_response = await async_client.post("/login", data={"username": TEST_USER["email"], "password": TEST_USER["password"]})
    ops_token = login_response.json()["access_token"]
    
    # Upload file
    test_file_content = b"test content"
    files = {"file": ("test.pptx", test_file_content, "application/vnd.openxmlformats-officedocument.presentationml.presentation")}
    headers = {"Authorization": f"Bearer {ops_token}"}
    upload_response = await async_client.post("/upload", files=files, headers=headers)
    file_id = upload_response.json()["file_id"]
    
    # Setup client user
    client_signup_response = await async_client.post("/signup", json=TEST_CLIENT)
    client_verify_url = client_signup_response.json()["secure_url"]
    client_token = client_verify_url.split("/")[-1]
    await async_client.get(f"/verify-email/{client_token}")
    login_response = await async_client.post("/login", data={"username": TEST_CLIENT["email"], "password": TEST_CLIENT["password"]})
    client_token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {client_token}"}
    
    # Get download link
    response = await async_client.get(f"/download-file/{file_id}", headers=headers)
    assert response.status_code == 200
    assert "download-link" in response.json()
    
    # Download file
    download_url = response.json()["download-link"]
    download_response = await async_client.get(download_url, headers=headers)
    assert download_response.status_code == 200 