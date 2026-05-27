// ----------------------------------------------------------------------
//  DASHBOARD : liste des pieces + creation manuelle + photos de stock
// ----------------------------------------------------------------------

const API_LIST = "/api/v1/parts/full";
const API_CREATE = "/api/v1/parts";
const API_STOCK_PHOTO = (id) => `/api/v1/parts/${id}/stock-photo`;

const container = document.getElementById("parts-container");
const hiddenFileInput = document.getElementById("hidden-file-input");

// ----------------------------------------------------------------------
//  UTILS
// ----------------------------------------------------------------------
function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    return String(text)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;").replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}

// ----------------------------------------------------------------------
//  RENDU DE LA LISTE
// ----------------------------------------------------------------------
function renderPartRow(part) {
    // --- Vignette CAO (cliquable -> viewer 3D) -----------------------
    let thumbnailCell;
    if (part.thumbnail_url) {
        const clickable = part.glb_url ? `onclick="openViewer(${part.id})"` : "";
        const cursor = part.glb_url ? "" : 'style="cursor:default"';
        thumbnailCell = `
            <div class="thumbnail-cell" ${clickable} ${cursor}
                 title="${part.glb_url ? "Cliquer pour voir en 3D" : "Pas de modèle 3D"}">
                <img src="${escapeHtml(part.thumbnail_url)}"
                     alt="${escapeHtml(part.part_name)}">
            </div>`;
    } else {
        thumbnailCell = `<div class="thumbnail-cell">
            <span class="placeholder">Pas de vignette</span>
        </div>`;
    }

    // --- Photo de stock : image + bouton "Remplacer", OU bouton "+" --
    // Si une photo existe deja, on l'affiche avec un petit lien
    // "Remplacer" en dessous. Sinon, un gros bouton d'ajout occupe
    // toute la cellule.
    let stockImgCell;
    if (part.stock_img_url) {
        stockImgCell = `
            <div class="stock-img-cell">
                <div class="stock-img-frame">
                    <img src="${escapeHtml(part.stock_img_url)}" alt="Stock">
                </div>
                <button class="btn-replace-photo"
                        onclick="triggerStockPhotoUpload(${part.id})">
                    Remplacer
                </button>
            </div>`;
    } else {
        stockImgCell = `
            <div class="stock-img-cell">
                <button class="btn-add-photo"
                        onclick="triggerStockPhotoUpload(${part.id})"
                        title="Ajouter une photo de la pièce en stock">
                    <span class="icon">📷</span>
                    <span>Ajouter</span>
                </button>
            </div>`;
    }

    // --- Quantite et location ---------------------------------------
    const qtyDisplay = (part.quantity === null || part.quantity === undefined)
        ? `<div class="quantity empty-value">—</div>`
        : `<div class="quantity">${part.quantity}</div>`;

    const locDisplay = part.location
        ? `<div class="location">${escapeHtml(part.location)}</div>`
        : `<div class="location empty-value">—</div>`;

    return `
        <div class="part-row" data-part-id="${part.id}">
            <div class="part-name">${escapeHtml(part.part_name)}</div>
            ${thumbnailCell}
            ${stockImgCell}
            ${qtyDisplay}
            ${locDisplay}
        </div>
    `;
}

window.openViewer = function(partId) {
    window.location.href = `/part.html?id=${partId}`;
};

async function loadParts() {
    try {
        const response = await fetch(API_LIST);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const parts = await response.json();

        if (parts.length === 0) {
            container.className = "empty";
            container.innerHTML = "Aucune pièce dans la base pour l'instant. "
                + "Cliquez sur « + Nouvelle pièce » ou exportez-en une depuis FreeCAD.";
            return;
        }

        container.className = "parts-list";
        container.innerHTML = parts.map(renderPartRow).join("");
    } catch (err) {
        container.className = "error";
        container.innerHTML = `Erreur lors du chargement : ${escapeHtml(err.message)}`;
        console.error(err);
    }
}

// ----------------------------------------------------------------------
//  MODAL "NOUVELLE PIECE"
// ----------------------------------------------------------------------
const modal = document.getElementById("modal-new-part");
const modalInput = document.getElementById("new-part-name");
const modalError = document.getElementById("modal-error");

function openModal() {
    modalInput.value = "";
    modalError.textContent = "";
    modal.style.display = "flex";
    // setTimeout pour laisser le DOM s'afficher avant le focus
    setTimeout(() => modalInput.focus(), 50);
}

function closeModal() {
    modal.style.display = "none";
}

async function confirmNewPart() {
    const name = modalInput.value.trim();
    if (!name) {
        modalError.textContent = "Le nom de la pièce est obligatoire.";
        return;
    }
    modalError.textContent = "";

    try {
        // L'endpoint attend du multipart/form-data (FastAPI Form(...)).
        // FormData fait exactement ca, c'est plus simple que JSON ici.
        const formData = new FormData();
        formData.append("part_name", name);

        const response = await fetch(API_CREATE, {
            method: "POST",
            body: formData,
        });

        if (!response.ok) {
            // L'API renvoie {detail: "..."} en cas d'erreur (400, 409...)
            const errBody = await response.json().catch(() => ({}));
            modalError.textContent = errBody.detail || `Erreur HTTP ${response.status}`;
            return;
        }

        const data = await response.json();
        console.log(`Pièce créée : id=${data.id} name=${data.part_name}`);
        closeModal();
        // On recharge la liste pour faire apparaitre la nouvelle ligne.
        await loadParts();
    } catch (err) {
        modalError.textContent = `Erreur : ${err.message}`;
        console.error(err);
    }
}

document.getElementById("btn-new-part").addEventListener("click", openModal);
document.getElementById("btn-cancel-new").addEventListener("click", closeModal);
document.getElementById("btn-confirm-new").addEventListener("click", confirmNewPart);
// Touche Entree dans le champ -> valide
modalInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") confirmNewPart();
    if (e.key === "Escape") closeModal();
});
// Clic sur le fond noir -> ferme (UX standard pour une modal)
modal.addEventListener("click", (e) => {
    if (e.target === modal) closeModal();
});

// ----------------------------------------------------------------------
//  UPLOAD DE PHOTO DE STOCK
// ----------------------------------------------------------------------
// On utilise UN SEUL input file cache, partage entre toutes les lignes.
// La piece concernee est memorisee dans une variable au moment du clic.
let pendingStockUploadPartId = null;

window.triggerStockPhotoUpload = function(partId) {
    pendingStockUploadPartId = partId;
    // Reset l'input pour que selectionner le MEME fichier deux fois
    // de suite redeclenche bien l'evenement 'change'.
    hiddenFileInput.value = "";
    hiddenFileInput.click();
};

hiddenFileInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    const partId = pendingStockUploadPartId;
    pendingStockUploadPartId = null;
    if (!file || !partId) return;

    try {
        const formData = new FormData();
        formData.append("photo", file);

        const response = await fetch(API_STOCK_PHOTO(partId), {
            method: "POST",
            body: formData,
        });

        if (!response.ok) {
            const errBody = await response.json().catch(() => ({}));
            alert(`Erreur lors de l'upload : ${errBody.detail || response.status}`);
            return;
        }

        // Recharge la liste pour afficher la nouvelle photo
        await loadParts();
    } catch (err) {
        alert(`Erreur : ${err.message}`);
        console.error(err);
    }
});

// ----------------------------------------------------------------------
//  DEMARRAGE
// ----------------------------------------------------------------------
loadParts();
