const progress = document.querySelector(".scroll-line span");
const glow = document.querySelector(".cursor-glow");
const revealItems = document.querySelectorAll(".reveal");

function updateProgress() {
  const height = document.documentElement.scrollHeight - window.innerHeight;
  progress.style.width = `${height > 0 ? (window.scrollY / height) * 100 : 0}%`;
}

const observer = new IntersectionObserver((entries) => {
  entries.forEach((entry) => {
    if (entry.isIntersecting) entry.target.classList.add("is-visible");
  });
}, { threshold: 0.13 });

revealItems.forEach((item) => observer.observe(item));
document.querySelectorAll("[data-play-flow]").forEach((button) => {
  button.addEventListener("click", () => {
    document.body.classList.remove("is-playing");
    window.setTimeout(() => document.body.classList.add("is-playing"), 20);
  });
});

window.addEventListener("pointermove", (event) => {
  glow.style.left = `${event.clientX}px`;
  glow.style.top = `${event.clientY}px`;
}, { passive: true });

updateProgress();
window.addEventListener("scroll", updateProgress, { passive: true });
