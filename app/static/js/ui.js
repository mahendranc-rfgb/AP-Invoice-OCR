// UI Module
export const toast = document.getElementById("toast");

export function showToast(message, type = "info") {
  if (!toast) return;
  toast.textContent = message;
  toast.className = `toast ${type} show`;
  setTimeout(() => {
    toast.className = "toast";
  }, 4000);
}

export function updateStepper(stepNumber) {
  for (let i = 1; i <= 4; i++) {
    const pill = document.getElementById(`step-pill-${i}`);
    if (pill) {
      if (i < stepNumber) {
        pill.className = "stepper-step done";
      } else if (i === stepNumber) {
        pill.className = "stepper-step active";
      } else {
        pill.className = "stepper-step";
      }
    }
  }
}

// Make accessible to inline HTML handlers
window.showToast = showToast;
window.updateStepper = updateStepper;
