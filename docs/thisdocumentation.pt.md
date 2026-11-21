# Esta documentação

Feita com [MkDocs](https://www.mkdocs.org/){target="_blank"} + [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/){target="_blank"},
a partir do [template de entrega do professor](https://hsandmann.github.io/documentation.template/){target="_blank"}.
As páginas incluem o código direto dos submódulos (`--8<-- "api/<serviço>/..."`), então o que aparece
aqui é sempre o código da versão referenciada pelo repositório principal.

## Rodar localmente

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

## Publicação

O workflow [`docs`](https://github.com/pna26/platform/blob/main/.github/workflows/docs.yaml){target="_blank"}
roda `mkdocs gh-deploy` a cada push na `main` e publica em
[pna26.github.io/platform](https://pna26.github.io/platform){target="_blank"}. Em pull requests ele só
roda `mkdocs build --strict`, para pegar link quebrado ou snippet que não existe antes do merge.

## Como editar

Cada integrante edita as páginas da sua parte em `docs/` (a navegação fica no `mkdocs.yml`) e abre um PR
no `pna26/platform`. Para atualizar o código mostrado de um serviço:

``` shell
git submodule update --remote api/<serviço>
git commit -am "docs: atualiza submódulo <serviço>"
```
