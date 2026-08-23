"""Unit tests for file discovery."""

from draftly.documentation.discovery import discover_documentation

DEFAULT_INCLUDE = ["README.md", "docs/**", "*.md", "*.mdx", "CHANGELOG.md", "CONTRIBUTING.md"]
DEFAULT_EXCLUDE = ["node_modules/**", "dist/**", "build/**", "vendor/**", ".git/**"]


def test_discover_default_includes_readme():
    paths = ["README.md", "src/main.py", "package.json"]
    result = discover_documentation(paths, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    assert "README.md" in result
    assert "src/main.py" not in result


def test_discover_default_includes_docs_directory():
    paths = ["docs/getting-started.md", "docs/api/reference.mdx", "src/index.ts"]
    result = discover_documentation(paths, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    assert "docs/getting-started.md" in result
    assert "docs/api/reference.mdx" in result
    assert "src/index.ts" not in result


def test_discover_excludes_node_modules():
    paths = ["node_modules/foo/README.md", "lib/README.md"]
    result = discover_documentation(paths, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    assert "node_modules/foo/README.md" not in result
    assert "lib/README.md" in result


def test_discover_custom_include():
    paths = ["guides/setup.md", "README.md"]
    include = ["guides/**"]
    result = discover_documentation(paths, include, DEFAULT_EXCLUDE)
    assert "guides/setup.md" in result
    assert "README.md" not in result


def test_discover_custom_exclude():
    paths = ["internal/secret.md", "public/docs.md"]
    exclude = ["internal/**"]
    result = discover_documentation(paths, DEFAULT_INCLUDE, exclude)
    assert "internal/secret.md" not in result
    assert "public/docs.md" in result


def test_discover_empty_paths():
    result = discover_documentation([], DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    assert result == []


def test_discover_no_matches():
    paths = ["src/main.ts", "lib/util.js"]
    result = discover_documentation(paths, DEFAULT_INCLUDE, DEFAULT_EXCLUDE)
    assert result == []
