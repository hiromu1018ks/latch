const intentText = document.querySelector("#intentText");
const root = document.documentElement;
const themeButton = document.querySelector("#themeButton");
const characterCount = document.querySelector("#characterCount");
const submitButton = document.querySelector("#submitButton");
const conditionList = document.querySelector("#conditionList");
const addConditionButton = document.querySelector("#addConditionButton");
const addConditionForm = document.querySelector("#addConditionForm");
const newConditionValue = document.querySelector("#newConditionValue");
const toast = document.querySelector("#toast");
const confirmationModal = document.querySelector("#confirmationModal");
const modalClose = document.querySelector("#modalClose");
const returnButton = document.querySelector("#returnButton");

const updateThemeButton = () => {
  const dark = root.dataset.theme === "dark";
  themeButton.setAttribute("aria-pressed", String(dark));
  themeButton.setAttribute("aria-label", dark ? "ライトモードに切り替える" : "ダークモードに切り替える");
  themeButton.querySelector("i").className = dark ? "ph ph-sun" : "ph ph-moon";
};

const setTheme = (theme, persist = false) => {
  root.dataset.theme = theme;
  if (persist) localStorage.setItem("latch-theme", theme);
  updateThemeButton();
};

themeButton.addEventListener("click", () => {
  setTheme(root.dataset.theme === "dark" ? "light" : "dark", true);
});

window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (event) => {
  if (!localStorage.getItem("latch-theme")) setTheme(event.matches ? "dark" : "light");
});

updateThemeButton();

const popovers = [
  {
    button: document.querySelector("#noticeButton"),
    panel: document.querySelector("#noticePopover"),
  },
  {
    button: document.querySelector("#accountButton"),
    panel: document.querySelector("#accountPopover"),
  },
];

const closePopovers = (except = null) => {
  popovers.forEach(({ button, panel }) => {
    if (panel !== except) {
      panel.hidden = true;
      button.setAttribute("aria-expanded", "false");
    }
  });
};

popovers.forEach(({ button, panel }) => {
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    const willOpen = panel.hidden;
    closePopovers(panel);
    panel.hidden = !willOpen;
    button.setAttribute("aria-expanded", String(willOpen));
  });
  panel.addEventListener("click", (event) => event.stopPropagation());
});

document.addEventListener("click", () => closePopovers());

const updateIntentState = () => {
  characterCount.textContent = `${intentText.value.length} / 300`;
  submitButton.disabled = !intentText.value.trim();
};

intentText.addEventListener("input", updateIntentState);
updateIntentState();

const createEditButton = (label) => {
  const button = document.createElement("button");
  button.className = "edit-button";
  button.type = "button";
  button.setAttribute("aria-label", `${label}を編集`);
  button.innerHTML = '<i class="ph ph-pencil-simple" aria-hidden="true"></i>';
  return button;
};

const beginConditionEdit = (row) => {
  if (row.querySelector(".condition-edit")) return;
  const label = row.dataset.label;
  const valueElement = row.querySelector(".condition-value");
  const editButton = row.querySelector(".edit-button");
  const originalValue = valueElement.textContent;
  const form = document.createElement("form");
  form.className = "condition-edit";
  form.innerHTML = `
    <input aria-label="${label}を編集" />
    <button type="submit" aria-label="変更を保存"><i class="ph ph-check" aria-hidden="true"></i></button>
  `;
  form.querySelector("input").value = originalValue;

  const finish = (save) => {
    const nextValue = form.querySelector("input").value.trim();
    if (save && nextValue) valueElement.textContent = nextValue;
    form.replaceWith(valueElement, editButton);
  };

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    finish(true);
  });
  form.querySelector("input").addEventListener("keydown", (event) => {
    if (event.key === "Escape") finish(false);
  });

  valueElement.remove();
  editButton.replaceWith(form);
  form.querySelector("input").focus();
  form.querySelector("input").select();
};

conditionList.addEventListener("click", (event) => {
  const editButton = event.target.closest(".edit-button");
  if (editButton) beginConditionEdit(editButton.closest(".condition-row"));
});

addConditionButton.addEventListener("click", () => {
  addConditionForm.hidden = false;
  addConditionButton.hidden = true;
  newConditionValue.focus();
});

document.querySelector("#cancelAdd").addEventListener("click", () => {
  addConditionForm.hidden = true;
  addConditionButton.hidden = false;
  newConditionValue.value = "";
});

addConditionForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const label = document.querySelector("#newConditionLabel").value;
  const value = newConditionValue.value.trim();
  if (!value) return;

  const row = document.createElement("div");
  row.className = "condition-row";
  row.dataset.label = label;
  row.innerHTML = `
    <div class="condition-icon"><i class="ph ph-flag" aria-hidden="true"></i></div>
    <span class="condition-label">${label}</span>
    <span class="condition-value"></span>
  `;
  row.querySelector(".condition-value").textContent = value;
  row.append(createEditButton(label));
  conditionList.append(row);

  newConditionValue.value = "";
  addConditionForm.hidden = true;
  addConditionButton.hidden = false;
});

let toastTimer;
document.querySelector("#draftButton").addEventListener("click", () => {
  window.clearTimeout(toastTimer);
  toast.hidden = false;
  toastTimer = window.setTimeout(() => {
    toast.hidden = true;
  }, 2400);
});

const openConfirmation = () => {
  if (!intentText.value.trim()) return;
  const expiry = document.querySelector("#expiry").value;
  const privacy = document.querySelector("#privacy").value;
  const summary = confirmationModal.querySelector(".confirmation-summary");
  summary.innerHTML = `
    <i class="ph ph-clock" aria-hidden="true"></i> ${expiry}まで
    <i class="ph ph-lock" aria-hidden="true"></i> ${privacy === "条件一致までは非公開" ? "成立までは非公開" : privacy}
  `;
  confirmationModal.hidden = false;
  document.body.classList.add("modal-open");
  modalClose.focus();
};

const closeConfirmation = () => {
  confirmationModal.hidden = true;
  document.body.classList.remove("modal-open");
  submitButton.focus();
};

submitButton.addEventListener("click", openConfirmation);
modalClose.addEventListener("click", closeConfirmation);
returnButton.addEventListener("click", closeConfirmation);
confirmationModal.addEventListener("click", (event) => {
  if (event.target === confirmationModal) closeConfirmation();
});

document.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    openConfirmation();
  }
  if (event.key === "Escape") {
    closePopovers();
    if (!confirmationModal.hidden) closeConfirmation();
  }
});
