# Dynamic Infrastructure Resilience & Accessibility Simulation

This repository is a polyglot monorepo containing the codebase, academic manuscripts, and presentation materials for analyzing infrastructure resilience and dynamic Robustness of Accessibility (RoA) during extreme flood events in Trier, Germany.

> **Project Origin:** The initial repository baseline was created by Kaub (`commit 4d156cb`). All subsequent commits build upon this foundation to introduce hydrodynamic flood stage modeling, cascading dual-resource (power/water) failure dynamics, and multi-tier resilience evaluations.

---

## Repository Structure

The monorepo is organized into the following modules:

* [**`code/`**](code/): Core Python package (`css_geodata_service`), simulation engine, and analysis notebooks managed via [`uv`](https://docs.astral.sh/uv/).
  * **Primary Demonstration Notebooks:**
    * [**`05_flood_simulation.ipynb`**](code/css_geodata_service/robustness_of_accessibility/examples/notebooks/05_flood_simulation.ipynb): Dynamic 14-day (336-hour) HQ100 flood progression simulation, modeling direct inundation, Network-Constrained Nearest-Neighbor (NCNN) cascading failures, and facility dual-resource Finite State Machine (FSM) buffers. Generates the interactive 4-panel HTML dashboard.
    * [**`07_flood_simulator_evaluation.ipynb`**](code/css_geodata_service/robustness_of_accessibility/examples/notebooks/07_flood_simulator_evaluation.ipynb): Quantitative multi-tier resilience evaluation computing time-dependent $RoA(t)$ and cumulative loss ($RoA_{\text{Int}}$), exporting paper figures and summary tables.
  * *Quickstart:* `cd code && uv sync`
* [**`paper/`**](paper/): Academic research paper manuscript written in LaTeX using the Springer LNCS template.
  * *Entry Point:* `paper/src/paper.tex`
* [**`expose/`**](expose/): Research proposal and exposé written in LaTeX.
  * *Entry Point:* `expose/src/expose.tex`
* [**`end-presentation/`**](end-presentation/): Final project presentation materials, including the standalone interactive simulation dashboard ([`Simulator-Nguyne-Welsch.html`](end-presentation/Simulator-Nguyne-Welsch.html)).
* [**`middle-presentation/`**](middle-presentation/): Mid-term presentation resources, project milestones, and concept structures.

---

## LaTeX Setup & Compilation

### Requirements
To compile the `.tex` documents in `paper/` and `expose/`, a standard LaTeX distribution is required:
* **Windows:** [MiKTeX](https://miktex.org/)
* **Linux:** [TeX Live](https://www.tug.org/texlive/)
* **macOS:** [MacTeX](https://www.tug.org/mactex/)

### Recommended IDE Support
* [**TeXiFy-IDEA**](https://plugins.jetbrains.com/plugin/9473-texify-idea): Recommended plugin for JetBrains IDEs (IntelliJ IDEA, PyCharm, CLion) providing syntax highlighting, autocompletion, and integrated build workflows.

### Document Architecture & Templates
* **Template:** Follows the [Springer LNCS Conference Proceedings Guidelines](https://www.springer.com/gp/computer-science/lncs/conference-proceedings-guidelines).
* **Structure:** Document sources inside `src/` are structured into front, body, and back matter, with `paper.tex` and `expose.tex` acting as the respective master documents.

---

## Usage of Generative AI & Digital Aids

In accordance with academic integrity guidelines, Generative AI tools (JetBrains Junie, OpenAI ChatGPT) were heavily leveraged throughout this research project as interactive digital assistants.

### Global Disclosure & Nature of Use
* **Global Declaration:** Because AI tools were utilized iteratively across multiple phases of the project, this central disclosure and the attached signed declaration serve as the repository-wide reference in lieu of individual in-text citations for every single prompt or assistance event.
* **Human-in-the-Loop Direction:** AI was never permitted to operate autonomously or generate project components independently from a single prompt. Every task, architectural design, algorithmic step, and implementation strategy was explicitly conceptualized and specified by the authors.
* **Review & Verification:** All AI outputs, generated code blocks, and draft formulations were inspected, validated, refactored, and tested by the authors to ensure scientific rigor and technical correctness.

### Scope of Assistance
* **Conceptual & Methodological Reflection:** Assisting in domain literature comprehension and critically stress-testing research designs.
* **Software Development:** Assisting in implementing author-specified software architectures, FSM logic, and geospatial processing pipelines under close human guidance and automated test validation.
* **Writing & Proofreading:** Refining grammar, academic tone, and phrasing of author-drafted manuscripts.

The complete signed declaration is available in the repository:
* [**Overview of Used Generative AI Tools and Digital Aids (Signed Declaration)**](Overview_of_Used_Generative_AI_Tools_and_Digital_Aids.jpeg)