// static/script.js

document.addEventListener("DOMContentLoaded", function() {
    const form = document.querySelector("form");

    form.addEventListener("submit", function(event) {
        const email = document.querySelector("input[name='email']").value;
        const password = document.querySelector("input[name='password']").value;

        if (email.trim() === "" || password.trim() === "") {
            alert("Please fill in all fields!");
            event.preventDefault(); // stop form submission
        }
    });
});

<script>
function openTab(evt, tabName) {
  const contents = document.querySelectorAll(".tab-content");
  const links = document.querySelectorAll(".tab-link");

  contents.forEach(c => c.classList.remove("active"));
  links.forEach(l => l.classList.remove("active"));

  document.getElementById(tabName).classList.add("active");
  evt.currentTarget.classList.add("active");
}
</script>

