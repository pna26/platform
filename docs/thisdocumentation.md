# This documentation

Built with [MkDocs](https://www.mkdocs.org/){target="_blank"} + [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/){target="_blank"},
based on the [professor's submission template](https://hsandmann.github.io/documentation.template/){target="_blank"}.
The pages include code directly from the submodules (`--8<-- "api/<serviço>/..."`), so what appears
here is always the code of the version referenced by the main repository.

## Run locally { #rodar-localmente }

=== "Linux/macOS"

    ``` shell
    git submodule update --init --recursive
    python3 -m venv env
    source ./env/bin/activate
    python3 -m pip install -r requirements.txt --upgrade
    mkdocs serve -o
    ```

=== "Windows"

    ``` shell
    git submodule update --init --recursive
    python -m venv env
    .\env\Scripts\activate
    python -m pip install -r requirements.txt --upgrade
    mkdocs serve -o
    ```

## Publishing { #publicacao }

The [`docs`](https://github.com/pna26/platform/blob/main/.github/workflows/docs.yaml){target="_blank"} workflow
runs `mkdocs gh-deploy` on every push to `main` and publishes to
[pna26.github.io/platform](https://pna26.github.io/platform){target="_blank"}. On pull requests it only
runs `mkdocs build --strict`, to catch broken links or missing snippets before the merge.

## How to edit { #como-editar }

Each member edits the pages for their part in `docs/` (navigation lives in `mkdocs.yml`) and opens a PR
on `pna26/platform`. To update the code shown for a service:

``` shell
git submodule update --remote api/<serviço>
git commit -am "docs: atualiza submódulo <serviço>"
```
