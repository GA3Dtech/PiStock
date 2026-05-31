# PiStock plugin — BOM dépliée (hierarchical tree view)
# Copyright (C) 2026 GA3Dtech — AGPLv3
#
# Premier plugin de demonstration : affiche n'importe quelle BOM sous
# forme d'arbre indente recursif, avec un tableau de totaux par piece
# feuille en bas. Lit le noyau via main.engine + main._flatten_bom,
# n'ecrit dans aucune table.
#
# Pour activer : il suffit que ce dossier (plugins/bom_tree/) soit
# present quand on demarre le serveur. Le noyau le decouvre et appelle
# register(app).

def register(app):
    """Point d'entree appele par le noyau au demarrage du serveur.
    'app' est l'instance FastAPI. On a aussi acces a nicegui.ui via
    un import standard."""
    from nicegui import ui

    @ui.page("/plugin/bom_tree")
    def bom_tree_page():
        # Imports tardifs : on s'assure que main est completement
        # charge au moment ou la page est rendue.
        import main
        from sqlmodel import Session, select

        # --- Header simple, visuellement aligne avec le reste ----
        # On ne reutilise pas render_app_header du noyau pour rester
        # independants ; un plugin peut tres bien avoir un look
        # different si l'auteur le souhaite.
        with ui.header().classes("bg-stone-800 text-white shadow"):
            with ui.row().classes("w-full items-center gap-3"):
                ui.label("🌳 BOM dépliée") \
                    .classes("text-xl font-medium")
                ui.element("div").classes("flex-grow")
                ui.button("← Plugins",
                           on_click=lambda: ui.navigate.to("/plugins")) \
                    .props("flat color=white").classes("text-sm")
                ui.button("🏠 Catalogue",
                           on_click=lambda: ui.navigate.to("/")) \
                    .props("flat color=white").classes("text-sm")

        # --- Recupere la liste des BOMs pour le selecteur ----------
        with Session(main.engine) as session:
            boms_rows = session.exec(
                select(main.Bom).order_by(main.Bom.code)
            ).all()
            bom_items = [
                {"id": b.id, "code": b.code,
                 "description": b.description or ""}
                for b in boms_rows
            ]

        with ui.column().classes("max-w-4xl mx-auto p-4 w-full gap-4"):
            if not bom_items:
                with ui.card().classes("w-full p-8 text-center"):
                    ui.label("Aucune BOM dans la base.") \
                        .classes("text-gray-500 italic")
                    ui.label("Créez une BOM depuis le catalogue, puis "
                             "revenez ici.") \
                        .classes("text-sm text-gray-400")
                return

            # Selecteur de BOM
            ui.label("Choisir une BOM à déplier :") \
                .classes("font-medium")
            bom_options = {
                b["id"]: (f"{b['code']} — {b['description'][:50]}"
                          if b["description"] else b["code"])
                for b in bom_items
            }
            selector = ui.select(options=bom_options,
                                  label="BOM", with_input=True) \
                .classes("w-full")

            # Conteneurs pour l'arbre et les totaux (re-remplis a
            # chaque changement du selecteur)
            tree_container = ui.column().classes("w-full gap-0 mt-2")
            totals_container = ui.column().classes("w-full gap-1 mt-4")

            def render():
                """Reconstruit l'arbre + le tableau de totaux pour
                la BOM selectionnee."""
                tree_container.clear()
                totals_container.clear()
                if not selector.value:
                    return
                bom_id = int(selector.value)

                with Session(main.engine) as session:
                    # --- 1. Arbre hierarchique ------------------
                    with tree_container:
                        bom = session.get(main.Bom, bom_id)
                        ui.label(f"📋 {bom.code} — "
                                  f"{bom.description or '(sans description)'}") \
                            .classes("text-lg font-bold border-b "
                                      "border-gray-300 pb-2 mb-2")
                        _render_subtree(session, bom_id, level=0,
                                          visited=set())

                    # --- 2. Totaux par piece feuille --------------
                    try:
                        totals = main._flatten_bom(session, bom_id)
                    except Exception as e:
                        with totals_container:
                            ui.label(f"⚠️ Erreur : {e}") \
                                .classes("text-red-600")
                        return

                    if not totals:
                        return

                    # Pre-charge les noms de pieces
                    parts_by_id = {
                        p.id: p.part_name for p in session.exec(
                            select(main.Parts)
                            .where(main.Parts.id.in_(totals.keys()))
                        ).all()
                    }

                    with totals_container:
                        ui.label("Totaux par pièce feuille") \
                            .classes("text-lg font-bold border-b "
                                      "border-gray-300 pb-2 mt-2")
                        ui.label(f"Pour assembler 1× {bom.code}, il "
                                 f"faut au total :") \
                            .classes("text-sm text-gray-600 mb-2")
                        # Tri par nom de piece pour la lisibilite
                        sorted_totals = sorted(
                            totals.items(),
                            key=lambda x: parts_by_id.get(x[0], "?").lower()
                        )
                        for pid, qty in sorted_totals:
                            name = parts_by_id.get(pid, f"#{pid}")
                            with ui.row().classes(
                                    "items-center gap-3 py-1 "
                                    "border-b border-gray-100"):
                                ui.label("📦").classes("text-sm")
                                ui.label(name) \
                                    .classes("text-sm flex-grow")
                                ui.label(f"×{qty}") \
                                    .classes("text-sm font-mono "
                                              "font-bold text-blue-700")

            selector.on_value_change(render)


def _render_subtree(session, bom_id, level, visited):
    """Affiche recursivement les lignes d'une BOM avec indentation.
    'visited' suit l'ensemble des BOMs deja traversees pour eviter
    les boucles (securite ; les cycles sont normalement refuses a
    l'insertion par le noyau)."""
    from nicegui import ui
    import main
    from sqlmodel import Session, select

    if bom_id in visited:
        with ui.row().classes("text-xs text-red-500 py-1") \
                .style(f"padding-left:{level * 24}px"):
            ui.label("⚠️ Cycle détecté — affichage interrompu.")
        return
    visited = visited | {bom_id}

    lines = session.exec(
        select(main.BomLine).where(main.BomLine.id_bom == bom_id)
        .order_by(main.BomLine.id)
    ).all()
    # Pre-charge parts et sous-BOMs referencees
    part_ids = {l.id_parts for l in lines if l.id_parts is not None}
    subbom_ids = {l.id_subbom for l in lines if l.id_subbom is not None}
    parts_by_id = {
        p.id: p for p in session.exec(
            select(main.Parts).where(main.Parts.id.in_(part_ids))
        ).all()
    } if part_ids else {}
    subboms_by_id = {
        b.id: b for b in session.exec(
            select(main.Bom).where(main.Bom.id.in_(subbom_ids))
        ).all()
    } if subbom_ids else {}

    indent_px = (level + 1) * 24

    for line in lines:
        with ui.row().classes(
                "w-full items-center gap-2 py-1 hover:bg-stone-50 "
                "border-l-2 border-gray-200") \
                .style(f"padding-left:{indent_px}px"):
            if line.id_parts is not None:
                part = parts_by_id.get(line.id_parts)
                name = part.part_name if part else f"#{line.id_parts}"
                ui.label("📦").classes("text-sm")
                ui.label(name).classes("text-sm flex-grow")
                ui.label(f"×{line.quantity}") \
                    .classes("text-sm font-mono text-gray-700 "
                              "font-bold")
            elif line.id_subbom is not None:
                sub = subboms_by_id.get(line.id_subbom)
                ui.label("📋").classes("text-sm")
                code = sub.code if sub else "?"
                desc = (sub.description if sub else "") or \
                       "(sans description)"
                ui.label(code).classes(
                    "text-xs font-mono font-bold text-blue-700 "
                    "bg-blue-100 px-2 py-0.5 rounded")
                ui.label(desc).classes("text-sm flex-grow")
                ui.label(f"×{line.quantity}").classes(
                    "text-sm font-mono text-blue-700 font-bold")

        # Recursion pour les sous-BOMs
        if line.id_subbom is not None:
            _render_subtree(session, line.id_subbom,
                             level + 1, visited)
