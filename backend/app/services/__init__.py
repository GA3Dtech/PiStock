# PiStock — services metier.
#
# Chaque module de ce package regroupe la logique et les endpoints REST
# d'un domaine (projects, boms, parts, stock, admin) + les helpers
# transverses (codes, generation de versions). main.py assemble les
# routers et re-exporte les symboles publics pour la compatibilite avec
# l'UI, les plugins et les tests (`main.Parts`, `main._flatten_bom`...).
