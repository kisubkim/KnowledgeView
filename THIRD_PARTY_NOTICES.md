# Third-party notices

KnowledgeView 실행 파일(`KnowledgeView-Setup.exe`)과 웹 화면에 포함된 구성요소입니다. 각 구성요소는 자기 라이선스를 따릅니다.

## 웹 화면 / 페이지 런타임

| 구성요소 | 라이선스 | 비고 |
| --- | --- | --- |
| [mermaid](https://github.com/mermaid-js/mermaid) 11.17.2 | MIT | `knowledgeview/static/vendor/mermaid.min.js`, 라이선스 전문 `mermaid.LICENSE` |

## 실행 파일에 포함된 라이브러리

| 라이브러리 | 라이선스 |
| --- | --- |
| [Python](https://www.python.org/) 3.12 (tkinter/Tcl/Tk 포함) | PSF License |
| FastAPI, pydantic, pydantic-core, annotated-types, annotated-doc, typing-inspection, anyio, h11, httptools, watchfiles, PyYAML, six, markdown-it-py, mdit-py-plugins, mdurl, truststore | MIT |
| Starlette, uvicorn, httpx, httpcore, idna, click, colorama, Jinja2, MarkupSafe, websockets, python-dotenv | BSD-3-Clause |
| Pygments | BSD-2-Clause |
| python-multipart | Apache-2.0 |
| certifi | MPL-2.0 (수정 없이 사용, 소스: https://github.com/certifi/python-certifi) |
| [Pillow](https://python-pillow.org/) | MIT-CMU (HPND) |
| [pystray](https://github.com/moses-palmer/pystray) | LGPL-3.0, 아래 안내 참고 |
| [PyInstaller](https://pyinstaller.org/) 부트로더 | GPL-2.0-or-later + bootloader exception. 예외 조항에 따라 생성된 실행 파일은 어떤 라이선스로든 배포할 수 있음 |

### pystray (LGPL-3.0) 안내

pystray는 수정하지 않고 사용했습니다. 원본 소스는 https://github.com/moses-palmer/pystray 에 있습니다.
LGPL에 따라 사용자는 pystray를 다른 버전으로 바꿔 실행할 수 있어야 합니다. 이 저장소의 소스와 `build.ps1`로 원하는 pystray 버전을 설치해 직접 빌드할 수 있고, `pip install -r requirements-desktop.txt` 후 `python -m knowledgeview --desktop`으로 소스에서 바로 실행할 수도 있습니다.
LGPL-3.0 전문: https://www.gnu.org/licenses/lgpl-3.0.html

## 생성 페이지에 들어가는 그림

Wikimedia Commons에서 가져온 그림은 각 파일의 라이선스(CC0, 퍼블릭 도메인, CC BY, CC BY-SA)를 따르며, 페이지의 그림 캡션과 "그림 내보내기"의 `그림_목록.md`에 출처, 작성자, 라이선스가 표시됩니다.

"Claude"는 Anthropic의 상표입니다. KnowledgeView는 Claude Code CLI를 호출할 뿐 Anthropic과 관련 없는 도구입니다.
