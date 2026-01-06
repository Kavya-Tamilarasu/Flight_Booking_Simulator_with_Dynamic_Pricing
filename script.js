async function searchFlights() {
    const origin = document.getElementById("origin").value.trim();
    const destination = document.getElementById("destination").value.trim();
    const date = document.getElementById("date").value;
    const loader = document.getElementById("loader");
    const results = document.getElementById("results");

    if (!origin || !destination || !date) {
        alert("Please fill all fields");
        return;
    }

    loader.classList.remove("hidden");
    results.innerHTML = "";

    const url = `http://127.0.0.1:8000/search?origin=${origin}&destination=${destination}&date_str=${date}`;

    try {
        const response = await fetch(url);
        const flights = await response.json();

        loader.classList.add("hidden");

        if (flights.length === 0) {
            results.innerHTML = "<p>No flights found.</p>";
            return;
        }

        flights.forEach(flight => {
            const card = document.createElement("div");
            card.className = "flight-card";

            card.innerHTML = `
                <div class="flight-info">
                    <h3>${flight.flight_number} • ${flight.airline}</h3>
                    <p>${flight.origin} ➜ ${flight.destination}</p>
                    <p>Departure: ${flight.departure_time}</p>
                    <p>Arrival: ${flight.arrival_time}</p>
                </div>
                <div class="price">₹${flight.dynamic_price}</div>
            `;

            results.appendChild(card);
        });

    } catch (error) {
        loader.classList.add("hidden");
        alert("Error fetching flights");
        console.error(error);
    }
}
