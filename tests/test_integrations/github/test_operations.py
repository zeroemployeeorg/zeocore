"""Tests for GitHub API _ops."""

import base64
from unittest.mock import MagicMock, patch

import pytest
import requests

from zeo_core.core.errors import ZeoApiError, ZeoError
from zeo_core.integrations.github.models import (
    GitHubRepo,
    GitHubUser,
    PullRequest,
    PullRequestStatus,
)
from zeo_core.integrations.github.operations import (
    add_issue_comment,
    check_repository_exists,
    create_issue,
    create_pull_request,
    fork_repo,
    get_issue,
    get_pull_request,
    get_pull_request_files,
    get_repo,
    get_repository_file_content,
    get_user,
    is_repo_starred,
    list_issues,
    list_pull_requests,
    star_repo,
    unstar_repo,
    update_repository_file,
)


@pytest.fixture
def mock_session() -> MagicMock:
    """Create a mock requests session."""
    session = MagicMock(spec=requests.Session)
    return session


@pytest.fixture
def mock_response() -> MagicMock:
    """Create a mock API response."""
    response = MagicMock()
    response.raise_for_status.return_value = None
    return response


class TestUserOperations:
    """Tests for user _ops."""

    def test_get_user_authenticated(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test getting the authenticated user."""
        # Mock API response
        user_data = {
            "login": "test_user",
            "html_url": "https://github.com/test_user",
            "name": "Test User",
            "email": "test@example.com",
            "avatar_url": "https://github.com/test_user.png",
        }
        mock_response.json.return_value = user_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.users.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_user(session=mock_session, api_url="https://api.github.com")

            # Verify result
            assert isinstance(result, GitHubUser)
            assert result.username == "test_user"
            assert str(result.url) == "https://github.com/test_user"
            assert result.name == "Test User"
            assert result.email == "test@example.com"
            assert str(result.avatar_url) == "https://github.com/test_user.png"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/user",
                api_url="https://api.github.com",
            )

    def test_get_user_specific(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test getting a specific user."""
        # Mock API response
        user_data = {
            "login": "other_user",
            "html_url": "https://github.com/other_user",
            "name": "Other User",
            "email": "other@example.com",
            "avatar_url": "https://github.com/other_user.png",
        }
        mock_response.json.return_value = user_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.users.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_user(
                session=mock_session,
                api_url="https://api.github.com",
                username="other_user",
            )

            # Verify result
            assert isinstance(result, GitHubUser)
            assert result.username == "other_user"
            assert str(result.url) == "https://github.com/other_user"
            assert result.name == "Other User"
            assert result.email == "other@example.com"
            assert str(result.avatar_url) == "https://github.com/other_user.png"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/users/other_user",
                api_url="https://api.github.com",
            )


class TestRepositoryOperations:
    """Tests for repository _ops."""

    def test_get_repo(self, mock_session: MagicMock, mock_response: MagicMock) -> None:
        """Test getting a repository."""
        # Mock API response
        repo_data = {
            "name": "test-repo",
            "full_name": "test_owner/test-repo",
            "html_url": "https://github.com/test_owner/test-repo",
            "clone_url": "https://github.com/test_owner/test-repo.git",
            "default_branch": "main",
            "description": "Test repository",
            "fork": False,
            "forks_count": 10,
            "stargazers_count": 100,
            "owner": {
                "login": "test_owner",
                "html_url": "https://github.com/test_owner",
                "avatar_url": "https://github.com/test_owner.png",
            },
        }
        mock_response.json.return_value = repo_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_repo(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert isinstance(result, GitHubRepo)
            assert result.name == "test-repo"
            assert result.full_name == "test_owner/test-repo"
            assert str(result.url) == "https://github.com/test_owner/test-repo"
            assert (
                str(result.clone_url) == "https://github.com/test_owner/test-repo.git"
            )
            assert result.default_branch == "main"
            assert result.description == "Test repository"
            assert result.fork is False
            assert result.forks_count == 10
            assert result.stargazers_count == 100
            assert result.owner.username == "test_owner"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_star_repo(self, mock_session: MagicMock, mock_response: MagicMock) -> None:
        """Test starring a repository."""
        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = star_repo(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is True

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="PUT",
                url="/user/starred/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_unstar_repo(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test unstarring a repository."""
        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = unstar_repo(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is True

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="DELETE",
                url="/user/starred/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_is_repo_starred_true(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test checking if a repository is starred (true case)."""
        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = is_repo_starred(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is True

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/user/starred/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_is_repo_starred_false(self, mock_session: MagicMock) -> None:
        """Test checking if a repository is starred (false case)."""
        # Mock make_request to raise a 404 error
        mock_error = ZeoApiError("Not found", status_code=404)

        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.side_effect = mock_error

            # Call operation
            result = is_repo_starred(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is False

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/user/starred/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_is_repo_starred_other_error(self, mock_session: MagicMock) -> None:
        """Test checking if a repository is starred with a non-404 error."""
        # Mock make_request to raise a non-404 error
        mock_error = ZeoApiError("API error", status_code=500)

        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.side_effect = mock_error

            # Call operation should re-raise the error
            with pytest.raises(ZeoApiError) as excinfo:
                is_repo_starred(
                    session=mock_session,
                    full_name="test_owner/test-repo",
                    api_url="https://api.github.com",
                )

            # Verify error
            assert "API error" in str(excinfo.value)

    def test_fork_repo(self, mock_session: MagicMock, mock_response: MagicMock) -> None:
        """Test forking a repository."""
        # Mock API response
        fork_data = {
            "name": "test-repo",
            "full_name": "test_user/test-repo",
            "html_url": "https://github.com/test_user/test-repo",
            "clone_url": "https://github.com/test_user/test-repo.git",
            "default_branch": "main",
            "description": "Test repository (fork)",
            "fork": True,
            "forks_count": 0,
            "stargazers_count": 0,
            "owner": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
        }
        mock_response.json.return_value = fork_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = fork_repo(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
                organization="test-org",
            )

            # Verify result
            assert isinstance(result, GitHubRepo)
            assert result.name == "test-repo"
            assert result.full_name == "test_user/test-repo"
            assert str(result.url) == "https://github.com/test_user/test-repo"
            assert str(result.clone_url) == "https://github.com/test_user/test-repo.git"
            assert result.fork is True
            assert result.owner.username == "test_user"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="POST",
                url="/repos/test_owner/test-repo/forks",
                api_url="https://api.github.com",
                json={"organization": "test-org"},
            )

    def test_check_repository_exists_true(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test checking if a repository exists (true case)."""
        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = check_repository_exists(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is True

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_check_repository_exists_false(self, mock_session: MagicMock) -> None:
        """Test checking if a repository exists (false case)."""
        # Mock make_request to raise a 404 error
        mock_error = ZeoApiError("Not found", status_code=404)

        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.side_effect = mock_error

            # Call operation
            result = check_repository_exists(
                session=mock_session,
                full_name="test_owner/test-repo",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result is False

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo",
                api_url="https://api.github.com",
            )

    def test_get_repository_file_content(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test getting repository file content."""
        # Mock API response
        file_content = "This is the file content"
        encoded_content = base64.b64encode(file_content.encode()).decode()

        file_data = {"content": encoded_content, "sha": "abc123"}
        mock_response.json.return_value = file_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            content, sha = get_repository_file_content(
                session=mock_session,
                repo="test_owner/test-repo",
                path="GET-STARTED.md",
                api_url="https://api.github.com",
                ref="main",
            )

            # Verify result
            assert content == "This is the file content"
            assert sha == "abc123"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/contents/GET-STARTED.md",
                api_url="https://api.github.com",
                params={"ref": "main"},
            )

    def test_update_repository_file(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test updating repository file."""
        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.repositories.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = update_repository_file(
                session=mock_session,
                repo="test_owner/test-repo",
                path="GET-STARTED.md",
                content="Updated content",
                message="Update README",
                sha="abc123",
                api_url="https://api.github.com",
                branch="main",
            )

            # Verify result
            assert result is True

            # Verify API call - check encoded content was sent
            mock_make_request.assert_called_once()
            call_args = mock_make_request.call_args

            assert call_args[1]["method"] == "PUT"
            assert (
                call_args[1]["url"]
                == "/repos/test_owner/test-repo/contents/GET-STARTED.md"
            )

            # Check JSON body contains encoded content
            json_data = call_args[1]["json"]
            assert json_data["message"] == "Update README"
            assert json_data["sha"] == "abc123"
            assert json_data["branch"] == "main"

            # Decode the content to check it matches the input
            encoded_content = json_data["content"]
            decoded_content = base64.b64decode(encoded_content).decode()
            assert decoded_content == "Updated content"


class TestPullRequestOperations:
    """Tests for pull request _ops."""

    def test_create_pull_request(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test creating a pull request."""
        # Mock API response
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "html_url": "https://github.com/test_owner/test-repo/pull/123",
            "user": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
            "state": "open",
            "body": "Test PR body",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
            "merged_at": None,
            "head": {"ref": "feature", "repo": {"full_name": "test_user/test-repo"}},
            "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
        }
        mock_response.json.return_value = pr_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = create_pull_request(
                session=mock_session,
                base_repo="test_owner/test-repo",
                head="test_user:feature",
                title="Test PR",
                api_url="https://api.github.com",
                body="Test PR body",
                base_branch="main",
            )

            # Verify result
            assert isinstance(result, PullRequest)
            assert result.number == 123
            assert result.title == "Test PR"
            assert str(result.url) == "https://github.com/test_owner/test-repo/pull/123"
            assert result.author.username == "test_user"
            assert result.status == PullRequestStatus.OPEN
            assert result.body == "Test PR body"
            assert result.base_repo == "test_owner/test-repo"
            assert result.head_repo == "test_user/test-repo"
            assert result.base_branch == "main"
            assert result.head_branch == "feature"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="POST",
                url="/repos/test_owner/test-repo/pulls",
                api_url="https://api.github.com",
                json={
                    "title": "Test PR",
                    "head": "test_user:feature",
                    "base": "main",
                    "body": "Test PR body",
                },
            )

    def test_list_pull_requests(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test listing pull requests."""
        # Mock API response
        pr_list = [
            {
                "number": 123,
                "title": "Test PR 1",
                "html_url": "https://github.com/test_owner/test-repo/pull/123",
                "user": {
                    "login": "test_user",
                    "html_url": "https://github.com/test_user",
                    "avatar_url": "https://github.com/test_user.png",
                },
                "state": "open",
                "body": "Test PR body 1",
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-01T00:00:00Z",
                "merged_at": None,
                "head": {
                    "ref": "feature1",
                    "repo": {"full_name": "test_user/test-repo"},
                },
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            },
            {
                "number": 124,
                "title": "Test PR 2",
                "html_url": "https://github.com/test_owner/test-repo/pull/124",
                "user": {
                    "login": "test_user",
                    "html_url": "https://github.com/test_user",
                    "avatar_url": "https://github.com/test_user.png",
                },
                "state": "open",
                "body": "Test PR body 2",
                "created_at": "2023-01-02T00:00:00Z",
                "updated_at": "2023-01-02T00:00:00Z",
                "merged_at": None,
                "head": {
                    "ref": "feature2",
                    "repo": {"full_name": "test_user/test-repo"},
                },
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            },
        ]
        mock_response.json.return_value = pr_list

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = list_pull_requests(
                session=mock_session,
                repo="test_owner/test-repo",
                api_url="https://api.github.com",
                state="open",
                author="test_user",
            )

            # Verify result
            assert isinstance(result, list)
            assert len(result) == 2

            # Check first PR
            assert isinstance(result[0], PullRequest)
            assert result[0].number == 123
            assert result[0].title == "Test PR 1"

            # Check second PR
            assert isinstance(result[1], PullRequest)
            assert result[1].number == 124
            assert result[1].title == "Test PR 2"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/pulls",
                api_url="https://api.github.com",
                params={"state": "open"},
            )

    def test_get_pull_request(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test getting a specific pull request."""
        # Mock API response
        pr_data = {
            "number": 123,
            "title": "Test PR",
            "html_url": "https://github.com/test_owner/test-repo/pull/123",
            "user": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
            "state": "open",
            "body": "Test PR body",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
            "merged_at": None,
            "head": {"ref": "feature", "repo": {"full_name": "test_user/test-repo"}},
            "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
        }
        mock_response.json.return_value = pr_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_pull_request(
                session=mock_session,
                repo="test_owner/test-repo",
                number=123,
                api_url="https://api.github.com",
            )

            # Verify result
            assert isinstance(result, PullRequest)
            assert result.number == 123
            assert result.title == "Test PR"
            assert str(result.url) == "https://github.com/test_owner/test-repo/pull/123"
            assert result.author.username == "test_user"
            assert result.status == PullRequestStatus.OPEN
            assert result.body == "Test PR body"
            assert result.base_repo == "test_owner/test-repo"
            assert result.head_repo == "test_user/test-repo"
            assert result.base_branch == "main"
            assert result.head_branch == "feature"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/pulls/123",
                api_url="https://api.github.com",
            )

    def test_get_pull_request_files(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test getting files from a pull request."""
        # Mock API response
        files_data = [
            {
                "filename": "GET-STARTED.md",
                "status": "modified",
                "additions": 10,
                "deletions": 2,
                "changes": 12,
            },
            {
                "filename": "src/main.py",
                "status": "added",
                "additions": 50,
                "deletions": 0,
                "changes": 50,
            },
        ]
        mock_response.json.return_value = files_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_pull_request_files(
                session=mock_session,
                repo="test_owner/test-repo",
                pull_number=123,
                api_url="https://api.github.com",
            )

            # Verify result
            assert isinstance(result, list)
            assert len(result) == 2
            assert result[0]["filename"] == "GET-STARTED.md"
            assert result[0]["status"] == "modified"
            assert result[1]["filename"] == "src/main.py"
            assert result[1]["status"] == "added"

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/pulls/123/files",
                api_url="https://api.github.com",
            )

    def test_create_pull_request_no_body(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test creating a pull request without a body -- covers the `if body:`
        skip branch (line 53) and the merged/closed status branches
        (lines 88-93) via a closed, non-merged PR."""
        pr_data = {
            "number": 124,
            "title": "No-body PR",
            "html_url": "https://github.com/test_owner/test-repo/pull/124",
            "user": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
            "state": "closed",
            "body": None,
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-01T00:00:00Z",
            "merged_at": None,
            "head": {"ref": "feature", "repo": {"full_name": "test_user/test-repo"}},
            "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
        }
        mock_response.json.return_value = pr_data

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = create_pull_request(
                session=mock_session,
                base_repo="test_owner/test-repo",
                head="test_user:feature",
                title="No-body PR",
                api_url="https://api.github.com",
            )

            assert result.status == PullRequestStatus.CLOSED
            assert result.body is None
            # No "body" key sent when body is falsy.
            call_json = mock_make_request.call_args[1]["json"]
            assert "body" not in call_json

    def test_create_pull_request_merged(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test creating a pull request that comes back already merged --
        covers the merged_at branch (lines 88-89)."""
        pr_data = {
            "number": 125,
            "title": "Merged PR",
            "html_url": "https://github.com/test_owner/test-repo/pull/125",
            "user": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
            "state": "closed",
            "body": "merged body",
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-02T00:00:00Z",
            "merged_at": "2023-01-02T00:00:00Z",
            "head": {"ref": "feature", "repo": {"full_name": "test_user/test-repo"}},
            "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
        }
        mock_response.json.return_value = pr_data

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = create_pull_request(
                session=mock_session,
                base_repo="test_owner/test-repo",
                head="test_user:feature",
                title="Merged PR",
                api_url="https://api.github.com",
                body="merged body",
            )

            assert result.status == PullRequestStatus.MERGED
            assert result.merged_at is not None

    def test_list_pull_requests_filters_by_author_and_statuses(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test listing pull requests exercises the author-mismatch skip
        (line 154-155) and the merged/closed/open status branches
        (lines 170-180) all in one real (boundary-mocked) call."""
        pr_list = [
            {
                # Wrong author -- must be filtered out.
                "number": 200,
                "title": "Not mine",
                "html_url": "https://github.com/test_owner/test-repo/pull/200",
                "user": {
                    "login": "someone_else",
                    "html_url": "https://github.com/someone_else",
                    "avatar_url": "https://github.com/someone_else.png",
                },
                "state": "open",
                "body": None,
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-01T00:00:00Z",
                "merged_at": None,
                "head": {"ref": "f0", "repo": {"full_name": "someone_else/test-repo"}},
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            },
            {
                # Merged.
                "number": 201,
                "title": "Merged one",
                "html_url": "https://github.com/test_owner/test-repo/pull/201",
                "user": {
                    "login": "test_user",
                    "html_url": "https://github.com/test_user",
                    "avatar_url": "https://github.com/test_user.png",
                },
                "state": "closed",
                "body": None,
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-02T00:00:00Z",
                "merged_at": "2023-01-02T00:00:00Z",
                "head": {"ref": "f1", "repo": {"full_name": "test_user/test-repo"}},
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            },
            {
                # Closed, not merged.
                "number": 202,
                "title": "Closed one",
                "html_url": "https://github.com/test_owner/test-repo/pull/202",
                "user": {
                    "login": "test_user",
                    "html_url": "https://github.com/test_user",
                    "avatar_url": "https://github.com/test_user.png",
                },
                "state": "closed",
                "body": None,
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-02T00:00:00Z",
                "merged_at": None,
                "head": {"ref": "f2", "repo": {"full_name": "test_user/test-repo"}},
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            },
        ]
        mock_response.json.return_value = pr_list

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = list_pull_requests(
                session=mock_session,
                repo="test_owner/test-repo",
                api_url="https://api.github.com",
                state="all",
                author="test_user",
            )

            # The wrong-author PR (200) was filtered out.
            assert [pr.number for pr in result] == [201, 202]
            assert result[0].status == PullRequestStatus.MERGED
            assert result[1].status == PullRequestStatus.CLOSED

    def test_get_pull_request_merged_and_closed_statuses(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test get_pull_request's merged/closed status branches
        (lines 246-256), calling it twice with distinct fixtures."""
        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            merged_data = {
                "number": 301,
                "title": "Merged",
                "html_url": "https://github.com/test_owner/test-repo/pull/301",
                "user": {
                    "login": "test_user",
                    "html_url": "https://github.com/test_user",
                    "avatar_url": "https://github.com/test_user.png",
                },
                "state": "closed",
                "body": None,
                "created_at": "2023-01-01T00:00:00Z",
                "updated_at": "2023-01-02T00:00:00Z",
                "merged_at": "2023-01-02T00:00:00Z",
                "head": {"ref": "f", "repo": {"full_name": "test_user/test-repo"}},
                "base": {"ref": "main", "repo": {"full_name": "test_owner/test-repo"}},
            }
            mock_response.json.return_value = merged_data
            mock_make_request.return_value = mock_response

            merged_result = get_pull_request(
                session=mock_session,
                repo="test_owner/test-repo",
                number=301,
                api_url="https://api.github.com",
            )
            assert merged_result.status == PullRequestStatus.MERGED

            closed_data = {**merged_data, "number": 302, "merged_at": None}
            mock_response.json.return_value = closed_data

            closed_result = get_pull_request(
                session=mock_session,
                repo="test_owner/test-repo",
                number=302,
                api_url="https://api.github.com",
            )
            assert closed_result.status == PullRequestStatus.CLOSED

    def test_merge_pull_request(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test merging a pull request -- covers merge_pull_request
        (lines 306-322) end to end, including the optional commit_title
        and commit_message request-body fields."""
        from zeo_core.integrations.github.operations.pull_requests import (
            merge_pull_request,
        )

        mock_response.json.return_value = {"merged": True, "sha": "abc123"}

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = merge_pull_request(
                session=mock_session,
                repo="test_owner/test-repo",
                pull_number=123,
                api_url="https://api.github.com",
                sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                commit_title="Merge it",
                commit_message="extra detail",
            )

            assert result is True
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="PUT",
                url="/repos/test_owner/test-repo/pulls/123/merge",
                api_url="https://api.github.com",
                json={
                    "merge_method": "merge",
                    "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "commit_title": "Merge it",
                    "commit_message": "extra detail",
                },
            )

    def test_merge_pull_request_not_merged(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test merge_pull_request's False-return path (a mergeable
        conflict reported by the API without an HTTP error) and the
        default-arguments branch (no commit_title/commit_message)."""
        from zeo_core.integrations.github.operations.pull_requests import (
            merge_pull_request,
        )

        mock_response.json.return_value = {"merged": False}

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = merge_pull_request(
                session=mock_session,
                repo="test_owner/test-repo",
                pull_number=456,
                api_url="https://api.github.com",
                sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )

            assert result is False
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="PUT",
                url="/repos/test_owner/test-repo/pulls/456/merge",
                api_url="https://api.github.com",
                json={
                    "merge_method": "merge",
                    "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                },
            )

    def test_add_pull_request_review(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test adding a review to a pull request -- covers
        add_pull_request_review (lines 384-394) end to end."""
        from zeo_core.integrations.github.operations.pull_requests import (
            add_pull_request_review,
        )

        review_data = {
            "id": 999,
            "state": "APPROVED",
            "body": "Looks good",
        }
        mock_response.json.return_value = review_data

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = add_pull_request_review(
                session=mock_session,
                repo="test_owner/test-repo",
                pull_number=123,
                body="Looks good",
                api_url="https://api.github.com",
                event="APPROVE",
            )

            assert result == review_data
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="POST",
                url="/repos/test_owner/test-repo/pulls/123/reviews",
                api_url="https://api.github.com",
                json={"body": "Looks good", "event": "APPROVE"},
            )


class TestGetPullRequestsByUser:
    """Tests for get_pull_requests_by_user (lines 397-504) -- the GitHub
    Search API path, entirely untested before this round."""

    def _make_search_item(
        self,
        number: int,
        state: str = "open",
    ) -> dict:
        return {
            "number": number,
            "title": f"PR {number}",
            "html_url": f"https://github.com/test_org/test-repo/pull/{number}",
            "body": "body text",
            "state": state,
            "user": {
                "login": "test_user",
                "html_url": "https://github.com/test_user",
                "avatar_url": "https://github.com/test_user.png",
            },
            "created_at": "2023-01-01T00:00:00Z",
            "updated_at": "2023-01-02T00:00:00Z",
        }

    def test_open_state_query_and_results(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """state='open' appends the is:open qualifier and results parse
        into PullRequest objects with empty repo/branch fields (the search
        API does not return head/base repo details)."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        mock_response.json.return_value = {
            "items": [self._make_search_item(1), self._make_search_item(2)]
        }

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = get_pull_requests_by_user(
                session=mock_session,
                username="test_user",
                org="test_org",
                api_url="https://api.github.com",
                state="open",
            )

            assert len(result) == 2
            assert all(isinstance(pr, PullRequest) for pr in result)
            assert result[0].status == PullRequestStatus.OPEN
            assert result[0].base_repo == ""
            assert result[0].head_repo == ""
            assert result[0].merged_at is None

            call_kwargs = mock_make_request.call_args[1]
            assert call_kwargs["params"] == {
                "q": "is:pr author:test_user org:test_org is:open"
            }

    def test_closed_state_query_and_status(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """state='closed' appends is:closed and a closed item maps to
        PullRequestStatus.CLOSED (the search API path never reports
        MERGED -- confirmed by reading the source: status is only OPEN or
        CLOSED here, unlike the REST endpoints above)."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        mock_response.json.return_value = {
            "items": [self._make_search_item(3, state="closed")]
        }

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = get_pull_requests_by_user(
                session=mock_session,
                username="test_user",
                org="test_org",
                api_url="https://api.github.com",
                state="closed",
            )

            assert len(result) == 1
            assert result[0].status == PullRequestStatus.CLOSED

            call_kwargs = mock_make_request.call_args[1]
            assert call_kwargs["params"] == {
                "q": "is:pr author:test_user org:test_org is:closed"
            }

    def test_all_state_no_qualifier_appended(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """state='all' (the else branch) appends no extra qualifier."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        mock_response.json.return_value = {"items": []}

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = get_pull_requests_by_user(
                session=mock_session,
                username="test_user",
                org="test_org",
                api_url="https://api.github.com",
                state="all",
            )

            assert result == []
            call_kwargs = mock_make_request.call_args[1]
            assert call_kwargs["params"] == {"q": "is:pr author:test_user org:test_org"}

    def test_make_request_failure_wraps_in_zeo_error(
        self, mock_session: MagicMock
    ) -> None:
        """The API-call failure path (lines 435-447): make_request raising
        is caught and re-raised as ZeoError, not left to propagate the
        original exception type."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.side_effect = RuntimeError("boom")

            with pytest.raises(ZeoError, match="Failed to search for pull requests"):
                get_pull_requests_by_user(
                    session=mock_session,
                    username="test_user",
                    org="test_org",
                    api_url="https://api.github.com",
                )

    def test_response_json_failure_wraps_in_zeo_error(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """The response-parsing failure path (lines 449-454): a real
        response.json() raising ValueError is caught and re-raised as
        ZeoError."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        mock_response.json.side_effect = ValueError("not json")

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            with pytest.raises(ZeoError, match="Failed to parse search results"):
                get_pull_requests_by_user(
                    session=mock_session,
                    username="test_user",
                    org="test_org",
                    api_url="https://api.github.com",
                )

    def test_malformed_item_skipped_not_fatal(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """A single malformed search-result item (missing created_at,
        so datetime.fromisoformat's .replace() call raises AttributeError
        on None) is caught per-item and skipped (lines 499-502) rather than
        aborting the whole call -- the well-formed sibling item still comes
        back."""
        from zeo_core.integrations.github.operations.pull_requests import (
            get_pull_requests_by_user,
        )

        malformed = self._make_search_item(4)
        malformed["created_at"] = None  # .replace() on None raises AttributeError

        mock_response.json.return_value = {
            "items": [malformed, self._make_search_item(5)]
        }

        with patch(
            "zeo_core.integrations.github.operations.pull_requests.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            result = get_pull_requests_by_user(
                session=mock_session,
                username="test_user",
                org="test_org",
                api_url="https://api.github.com",
            )

            # Only the well-formed item survives; the malformed one is
            # logged and skipped, not fatal to the whole call.
            assert len(result) == 1
            assert result[0].number == 5


class TestIssueOperations:
    """Tests for issue _ops."""

    def test_create_issue(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test creating an issue."""
        # Mock API response
        issue_data = {
            "number": 42,
            "title": "Test Issue",
            "html_url": "https://github.com/test_owner/test-repo/issues/42",
            "body": "Issue description",
            "user": {"login": "test_user", "html_url": "https://github.com/test_user"},
            "labels": [{"name": "bug"}, {"name": "help wanted"}],
            "assignees": [{"login": "test_user"}],
        }
        mock_response.json.return_value = issue_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.issues.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = create_issue(
                session=mock_session,
                repo="test_owner/test-repo",
                title="Test Issue",
                api_url="https://api.github.com",
                body="Issue description",
                labels=["bug", "help wanted"],
                assignees=["test_user"],
            )

            # Verify result
            assert result == issue_data

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="POST",
                url="/repos/test_owner/test-repo/issues",
                api_url="https://api.github.com",
                json={
                    "title": "Test Issue",
                    "body": "Issue description",
                    "labels": ["bug", "help wanted"],
                    "assignees": ["test_user"],
                },
            )

    def test_list_issues(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test listing issues."""
        # Mock API response
        issues_data = [
            {
                "number": 42,
                "title": "Test Issue 1",
                "html_url": "https://github.com/test_owner/test-repo/issues/42",
                "state": "open",
            },
            {
                "number": 43,
                "title": "Test Issue 2",
                "html_url": "https://github.com/test_owner/test-repo/issues/43",
                "state": "open",
            },
        ]
        mock_response.json.return_value = issues_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.issues.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = list_issues(
                session=mock_session,
                repo="test_owner/test-repo",
                api_url="https://api.github.com",
                state="open",
                labels="bug",
                sort="created",
                direction="desc",
            )

            # Verify result
            assert result == issues_data

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/issues",
                api_url="https://api.github.com",
                params={
                    "state": "open",
                    "labels": "bug",
                    "sort": "created",
                    "direction": "desc",
                },
            )

    def test_get_issue(self, mock_session: MagicMock, mock_response: MagicMock) -> None:
        """Test getting an issue."""
        # Mock API response
        issue_data = {
            "number": 42,
            "title": "Test Issue",
            "html_url": "https://github.com/test_owner/test-repo/issues/42",
            "body": "Issue description",
            "user": {"login": "test_user", "html_url": "https://github.com/test_user"},
            "state": "open",
        }
        mock_response.json.return_value = issue_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.issues.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = get_issue(
                session=mock_session,
                repo="test_owner/test-repo",
                issue_number=42,
                api_url="https://api.github.com",
            )

            # Verify result
            assert result == issue_data

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="GET",
                url="/repos/test_owner/test-repo/issues/42",
                api_url="https://api.github.com",
            )

    def test_add_issue_comment(
        self, mock_session: MagicMock, mock_response: MagicMock
    ) -> None:
        """Test adding a comment to an issue."""
        # Mock API response
        comment_data = {
            "id": 123,
            "body": "Test comment",
            "user": {"login": "test_user", "html_url": "https://github.com/test_user"},
            "created_at": "2023-01-01T00:00:00Z",
        }
        mock_response.json.return_value = comment_data

        # Mock make_request
        with patch(
            "zeo_core.integrations.github.operations.issues.make_request"
        ) as mock_make_request:
            mock_make_request.return_value = mock_response

            # Call operation
            result = add_issue_comment(
                session=mock_session,
                repo="test_owner/test-repo",
                issue_number=42,
                body="Test comment",
                api_url="https://api.github.com",
            )

            # Verify result
            assert result == comment_data

            # Verify API call
            mock_make_request.assert_called_once_with(
                session=mock_session,
                method="POST",
                url="/repos/test_owner/test-repo/issues/42/comments",
                api_url="https://api.github.com",
                json={"body": "Test comment"},
            )
