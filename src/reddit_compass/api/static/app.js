// reddit-compass UI enhancements
document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss status badges after animation
    const badges = document.querySelectorAll('.status-badge');
    badges.forEach(badge => {
        badge.addEventListener('animationend', () => {
            badge.classList.add('settled');
        });
    });

    // Keyboard navigation for story cards
    const cards = document.querySelectorAll('.story-card');
    cards.forEach(card => {
        card.setAttribute('tabindex', '0');
        card.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                const link = card.querySelector('.story-title a');
                if (link) link.click();
            }
        });
    });
});
