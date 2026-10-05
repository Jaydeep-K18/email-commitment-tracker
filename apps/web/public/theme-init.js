// Runs before the app loads: pick the theme from the last saved preference,
// falling back to the operating system's, so the first paint is already right.
(function () {
  try {
    var saved = localStorage.getItem("commitmail.theme");
    var dark = saved === "dark" || ((!saved || saved === "system") &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  } catch (e) {
    /* storage blocked: the app applies the theme once it starts */
  }
})();
