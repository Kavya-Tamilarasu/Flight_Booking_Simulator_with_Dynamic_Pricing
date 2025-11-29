-- Drop everything first
DROP VIEW IF EXISTS flight_search_view;
DROP TRIGGER IF EXISTS trg_update_dynamic_pricing_on_insert_passenger;
DROP TABLE IF EXISTS payment;
DROP TABLE IF EXISTS passenger;
DROP TABLE IF EXISTS flight_price_history;
DROP TABLE IF EXISTS cancelled_booking;
DROP TABLE IF EXISTS booking;
DROP TABLE IF EXISTS flight;
DROP TABLE IF EXISTS airport_lookup;
DROP TABLE IF EXISTS user;

-- User Table (unchanged - correct)
CREATE TABLE user (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username VARCHAR(20) NOT NULL UNIQUE,
    password_hash VARCHAR(128) NOT NULL,
    full_name VARCHAR(100),
    phone VARCHAR(20),
    country VARCHAR(50),
    role VARCHAR(10) DEFAULT 'CUSTOMER' CHECK (role IN ('ADMIN', 'CUSTOMER')),
    created_date DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Airport Lookup (ADDED DOH)
CREATE TABLE airport_lookup (
    code VARCHAR(10) PRIMARY KEY,
    city_country VARCHAR(100) NOT NULL,
    timezone VARCHAR(50)
);

-- Flight Table (CORRECTED demand_factor constraint)
CREATE TABLE flight (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_number VARCHAR(50) NOT NULL UNIQUE,
    airline VARCHAR(50) NOT NULL,
    from_airport_code VARCHAR(10) NOT NULL,
    to_airport_code VARCHAR(10) NOT NULL,
    departure_time DATETIME NOT NULL,
    arrival_time DATETIME NOT NULL,
    base_price DECIMAL(10,2) NOT NULL,
    total_seats INTEGER NOT NULL,
    seats_remaining INTEGER NOT NULL DEFAULT 0 CHECK(seats_remaining >= 0 AND seats_remaining <= total_seats),
    demand_factor DECIMAL(3,2) DEFAULT 1.00 CHECK(demand_factor >= 0.80 AND demand_factor <= 1.60),
    created_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (from_airport_code) REFERENCES airport_lookup(code),
    FOREIGN KEY (to_airport_code) REFERENCES airport_lookup(code)
);

-- Rest of tables unchanged (all correct)
CREATE TABLE booking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    flight_id INTEGER NOT NULL,
    pnr VARCHAR(10) NOT NULL UNIQUE,
    booking_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','CONFIRMED','CANCELLED','PENDING_PAYMENT')),
    payment_reference VARCHAR(50),
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (flight_id) REFERENCES flight(id)
);

CREATE TABLE passenger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    flight_id INTEGER NOT NULL,
    seat_number VARCHAR(10) NOT NULL,
    seat_type VARCHAR(10) CHECK (seat_type IN ('WINDOW', 'AISLE', 'MIDDLE')),
    full_name VARCHAR(100) NOT NULL,
    age INTEGER,
    contact_phone VARCHAR(20),
    contact_email VARCHAR(100),
    FOREIGN KEY (booking_id) REFERENCES booking(id),
    FOREIGN KEY (flight_id) REFERENCES flight(id),
    UNIQUE (flight_id, seat_number)
);

CREATE TABLE cancelled_booking (
    archive_id INTEGER PRIMARY KEY AUTOINCREMENT,
    pnr VARCHAR(10),
    user_id INTEGER NOT NULL,
    flight_id INTEGER NOT NULL,
    refund_amount DECIMAL(10,2),
    cancellation_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    cancellation_reason VARCHAR(100),
    passenger_full_name VARCHAR(100),
    FOREIGN KEY (user_id) REFERENCES user(id),
    FOREIGN KEY (flight_id) REFERENCES flight(id)
);

CREATE TABLE flight_price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flight_id INTEGER NOT NULL,
    recorded_price DECIMAL(10,2) NOT NULL,
    demand_factor DECIMAL(3,2) NOT NULL,
    seats_remaining INTEGER NOT NULL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (flight_id) REFERENCES flight(id)
);

CREATE TABLE payment (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    booking_id INTEGER NOT NULL,
    payment_reference VARCHAR(50) UNIQUE,
    payment_method VARCHAR(20) CHECK (payment_method IN ('UPI', 'CARD', 'WALLET', 'NETBANKING')),
    amount_paid DECIMAL(10,2) NOT NULL,
    payment_status VARCHAR(20) CHECK (payment_status IN ('SUCCESS', 'FAILED', 'PENDING')),
    payment_date DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (booking_id) REFERENCES booking(id)
);

-- ✅ SIMPLIFIED SQLite-Compatible Trigger (ONLY decreases seats)
CREATE TRIGGER trg_update_seats_remaining
AFTER INSERT ON passenger
FOR EACH ROW
BEGIN
    UPDATE flight SET seats_remaining = seats_remaining - 1 WHERE id = NEW.flight_id;
END;

-- Indexes (all correct)
CREATE INDEX idx_flight_route ON flight(from_airport_code, to_airport_code, departure_time);
CREATE INDEX idx_flight_departure ON flight(departure_time);
CREATE INDEX idx_flight_airline ON flight(airline);
CREATE INDEX idx_booking_pnr ON booking(pnr);
CREATE INDEX idx_booking_user ON booking(user_id);
CREATE INDEX idx_booking_status ON booking(status);
CREATE INDEX idx_booking_date ON booking(booking_date);
CREATE INDEX idx_passenger_booking ON passenger(booking_id);
CREATE INDEX idx_price_history_flight ON flight_price_history(flight_id);
CREATE INDEX idx_price_history_timestamp ON flight_price_history(timestamp);

-- View (CORRECT)
CREATE VIEW flight_search_view AS
SELECT 
    f.id, f.flight_number, f.airline, f.from_airport_code,
    fa.city_country AS from_city_country, f.to_airport_code,
    ta.city_country AS to_city_country, f.departure_time, f.arrival_time,
    f.base_price, f.total_seats, f.seats_remaining, f.demand_factor,
    ROUND(f.base_price * f.demand_factor * (1 + (1 - (f.seats_remaining * 1.0 / f.total_seats)) * 0.5), 2) AS current_price
FROM flight f
JOIN airport_lookup fa ON f.from_airport_code = fa.code
JOIN airport_lookup ta ON f.to_airport_code = ta.code;

-- Sample Data Insertions

-- Users
INSERT INTO user (username, password_hash, full_name, phone, country, role) VALUES
('mentor_user', 'hashedpassword123', 'Mentor Reviewer', '9991112220', 'USA', 'ADMIN'),
('testuser', 'hashedpassword123', 'Test User Default', '9992223330', 'UK', 'CUSTOMER'),
('Ali', 'hashedpassword123', 'Ali Hassan', '9876543210', 'India', 'CUSTOMER'),
('JaneDoe', 'hashedpassword123', 'Jane Doe', '1112223333', 'Canada', 'CUSTOMER'),
('JohnSmith', 'hashedpassword123', 'John Smith', '4445556666', 'Australia', 'CUSTOMER'),
('MariaGarcia', 'hashedpassword123', 'Maria Garcia', '7778889999', 'Spain', 'CUSTOMER');

-- Airports
INSERT INTO airport_lookup (code, city_country, timezone) VALUES
('JFK', 'New York, USA', 'America/New_York'), 
('LHR', 'London, UK', 'Europe/London'),
('LAX', 'Los Angeles, USA', 'America/Los_Angeles'),
('DXB', 'Dubai, UAE', 'Asia/Dubai'),
('CDG', 'Paris, France', 'Europe/Paris'),
('NRT', 'Tokyo, Japan', 'Asia/Tokyo'),
('SYD', 'Sydney, Australia', 'Australia/Sydney'),
('SIN', 'Singapore, Singapore', 'Asia/Singapore'),
('HKG', 'Hong Kong, China', 'Asia/Hong_Kong'),
('FRA', 'Frankfurt, Germany', 'Europe/Berlin'),
('MUC', 'Munich, Germany', 'Europe/Berlin'),
('BKK', 'Bangkok, Thailand', 'Asia/Bangkok'),
('SGN', 'Ho Chi Minh City, Vietnam', 'Asia/Ho_Chi_Minh'),
('ORD', 'Chicago, USA', 'America/Chicago'),
('MIA', 'Miami, USA', 'America/New_York'),
('AMS', 'Amsterdam, Netherlands', 'Europe/Amsterdam'),
('CPH', 'Copenhagen, Denmark', 'Europe/Copenhagen'),
('DEL', 'New Delhi, India', 'Asia/Kolkata'),
('BOM', 'Mumbai, India', 'Asia/Kolkata'),
('PEK', 'Beijing, China', 'Asia/Shanghai'),
('PVG', 'Shanghai, China', 'Asia/Shanghai'),
('KUL', 'Kuala Lumpur, Malaysia', 'Asia/Kuala_Lumpur'),
('MEX', 'Mexico City, Mexico', 'America/Mexico_City'),
('CUN', 'Cancun, Mexico', 'America/Cancun'),
('DUB', 'Dublin, Ireland', 'Europe/Dublin'),
('IST', 'Istanbul, Turkey', 'Europe/Istanbul'),
('GIG', 'Rio de Janeiro, Brazil', 'America/Sao_Paulo'),
('JNB', 'Johannesburg, South Africa', 'Africa/Johannesburg'),
('YVR', 'Vancouver, Canada', 'America/Vancouver'),
('ICN', 'Seoul, South Korea', 'Asia/Seoul'),
('ATH', 'Athens, Greece', 'Europe/Athens'),
('FCO', 'Rome, Italy', 'Europe/Rome'),
('SCL', 'Santiago, Chile', 'America/Santiago'),
('YUL', 'Montreal, Canada', 'America/Montreal'),
('PER', 'Perth, Australia', 'Australia/Perth'),
('KWI', 'Kuwait City, Kuwait', 'Asia/Kuwait'),
('BLR', 'Bengaluru, India', 'Asia/Kolkata'),
('MAA', 'Chennai, India', 'Asia/Kolkata'),
('CCU', 'Kolkata, India', 'Asia/Kolkata'),
('HYD', 'Hyderabad, India', 'Asia/Kolkata'),
('GOI', 'Goa, India', 'Asia/Kolkata'),
('JAI', 'Jaipur, India', 'Asia/Kolkata'),
('PAT', 'Patna, India', 'Asia/Kolkata'),
('COK', 'Kochi, India', 'Asia/Kolkata'),
('GAU', 'Guwahati, India', 'Asia/Kolkata'),
('SXR', 'Srinagar, India', 'Asia/Kolkata'),
('PNQ', 'Pune, India', 'Asia/Kolkata'),
('AMD', 'Ahmedabad, India', 'Asia/Kolkata'),
('LKO', 'Lucknow, India', 'Asia/Kolkata'),
('YYZ', 'Toronto, Canada', 'America/Toronto'),
('DFW', 'Dallas, USA', 'America/Chicago'),
('SEA', 'Seattle, USA', 'America/Los_Angeles'),
('BOS', 'Boston, USA', 'America/New_York'),
('MAD', 'Madrid, Spain', 'Europe/Madrid'),
('ZRH', 'Zurich, Switzerland', 'Europe/Zurich'),
('VIE', 'Vienna, Austria', 'Europe/Vienna'),
('BRU', 'Brussels, Belgium', 'Europe/Brussels'),
('ARN', 'Stockholm, Sweden', 'Europe/Stockholm'),
('OSL', 'Oslo, Norway', 'Europe/Oslo');

-- Flights (Only a few samples for brevity)
INSERT INTO flight (flight_number, airline, from_airport_code, to_airport_code, departure_time, arrival_time, base_price, total_seats, seats_remaining, demand_factor) VALUES
('FL001', 'United Airlines', 'JFK', 'LHR', '2025-12-10 18:00:00', '2025-12-11 06:00:00', 550.00, 200, 150, 1.1),
('FL002', 'Emirates', 'LAX', 'DXB', '2025-12-12 20:00:00', '2025-12-13 22:00:00', 1200.50, 300, 280, 1.0),
('FL003', 'Air France', 'CDG', 'NRT', '2025-12-15 10:30:00', '2025-12-16 07:00:00', 980.25, 250, 220, 1.2),
('FL004', 'United Airlines', 'LHR', 'JFK', '2025-12-10 12:00:00', '2025-12-10 15:00:00', 600.00, 200, 180, 1.1),
('FL005', 'Qantas', 'DXB', 'SYD', '2025-12-18 01:00:00', '2025-12-18 22:00:00', 1500.75, 400, 350, 1.3),
('FL006', 'Emirates', 'NRT', 'CDG', '2025-12-16 11:00:00', '2025-12-16 19:00:00', 950.00, 250, 200, 1.1),
('FL007', 'Delta Airlines', 'JFK', 'CDG', '2025-12-20 08:00:00', '2025-12-20 16:00:00', 450.00, 180, 100, 1.4),
('FL008', 'United Airlines', 'SYD', 'LAX', '2025-12-22 14:00:00', '2025-12-22 11:00:00', 1300.00, 400, 390, 1.0),
('FL009', 'Singapore Airlines', 'SIN', 'HKG', '2025-12-25 19:00:00', '2025-12-25 23:00:00', 250.00, 150, 140, 1.0),
('FL010', 'Air France', 'HKG', 'SIN', '2025-12-26 09:00:00', '2025-12-26 13:00:00', 280.00, 150, 120, 1.1),
('FL011', 'Lufthansa', 'FRA', 'MUC', '2025-12-05 08:30:00', '2025-12-05 09:30:00', 150.00, 100, 80, 1.0),
('FL012', 'Thai Airways', 'BKK', 'SGN', '2025-12-06 14:00:00', '2025-12-06 15:30:00', 320.00, 120, 110, 1.0),
('FL013', 'Thai Airways', 'SGN', 'BKK', '2025-12-07 16:00:00', '2025-12-07 17:30:00', 300.00, 120, 90, 1.1),
('FL014', 'American Airlines', 'ORD', 'MIA', '2025-12-08 10:00:00', '2025-12-08 13:00:00', 350.00, 220, 200, 1.0),
('FL015', 'American Airlines', 'MIA', 'ORD', '2025-12-09 14:00:00', '2025-12-09 17:00:00', 380.00, 220, 190, 1.0),
('FL016', 'KLM', 'AMS', 'CPH', '2025-12-10 17:00:00', '2025-12-10 18:30:00', 200.00, 110, 50, 1.3),
('FL017', 'SAS', 'CPH', 'AMS', '2025-12-11 19:00:00', '2025-12-11 20:30:00', 210.00, 110, 60, 1.2),
('FL018', 'IndiGo', 'DEL', 'BOM', '2025-12-12 06:00:00', '2025-12-12 08:00:00', 120.00, 160, 150, 1.0),
('FL019', 'Air India', 'BOM', 'DEL', '2025-12-13 09:00:00', '2025-12-13 11:00:00', 130.00, 160, 140, 1.0),
('FL020', 'Air China', 'PEK', 'PVG', '2025-12-14 13:00:00', '2025-12-14 15:00:00', 180.00, 280, 250, 1.0),
('FL021', 'China Eastern', 'PVG', 'PEK', '2025-12-15 16:00:00', '2025-12-15 18:00:00', 190.00, 280, 240, 1.0),
('FL022', 'Qatar Airways', 'DXB', 'KUL', '2025-12-16 22:30:00', '2025-12-17 09:30:00', 800.00, 320, 310, 1.0),
('FL023', 'Qatar Airways', 'KUL', 'DXB', '2025-12-18 10:30:00', '2025-12-18 17:30:00', 850.00, 320, 300, 1.0),
('FL024', 'Aeroméxico', 'MEX', 'CUN', '2025-12-19 11:00:00', '2025-12-19 13:00:00', 250.00, 180, 170, 1.0),
('FL025', 'Viva Aerobus', 'CUN', 'MEX', '2025-12-20 14:00:00', '2025-12-20 16:00:00', 270.00, 180, 160, 1.0),
('FL026', 'United Airlines', 'JFK', 'DUB', '2025-12-21 17:00:00', '2025-12-22 05:00:00', 480.00, 190, 140, 1.1),
('FL027', 'Lufthansa', 'FRA', 'IST', '2025-12-22 08:30:00', '2025-12-22 12:00:00', 350.00, 160, 120, 1.1),
('FL028', 'Emirates', 'DXB', 'GIG', '2025-12-23 01:00:00', '2025-12-23 15:00:00', 1450.00, 350, 300, 1.2),
('FL029', 'Qantas', 'SYD', 'JNB', '2025-12-24 23:00:00', '2025-12-25 09:00:00', 1100.00, 280, 250, 1.1),
('FL030', 'Air Canada', 'YVR', 'NRT', '2025-12-25 13:00:00', '2025-12-26 15:00:00', 850.00, 220, 170, 1.1),
('FL031', 'Singapore Airlines', 'SIN', 'ICN', '2025-12-26 10:00:00', '2025-12-26 17:00:00', 580.00, 240, 200, 1.0),
('FL032', 'Emirates', 'GIG', 'DXB', '2025-12-27 18:00:00', '2025-12-28 16:00:00', 1500.00, 350, 310, 1.1),
('FL033', 'KLM', 'AMS', 'JFK', '2025-12-28 11:00:00', '2025-12-28 14:00:00', 520.00, 210, 160, 1.2),
('FL034', 'Air France', 'CDG', 'GIG', '2025-12-29 15:00:00', '2025-12-29 23:00:00', 1150.00, 300, 280, 1.0),
('FL035', 'Lufthansa', 'MUC', 'LHR', '2025-12-30 07:00:00', '2025-12-30 08:30:00', 180.00, 120, 90, 1.1),
('FL036', 'United Airlines', 'ORD', 'JFK', '2025-12-31 16:00:00', '2025-12-31 19:00:00', 220.00, 250, 210, 1.0),
('FL037', 'IndiGo', 'BOM', 'BKK', '2026-01-01 21:00:00', '2026-01-02 02:00:00', 280.00, 180, 150, 1.2),
('FL038', 'Thai Airways', 'BKK', 'BOM', '2026-01-02 03:00:00', '2026-01-02 06:00:00', 290.00, 180, 160, 1.1),
('FL039', 'American Airlines', 'JFK', 'LAX', '2026-01-03 10:00:00', '2026-01-03 13:00:00', 390.00, 320, 300, 1.0),
('FL040', 'Delta Airlines', 'LAX', 'JFK', '2026-01-04 14:00:00', '2026-01-04 19:00:00', 410.00, 320, 290, 1.0),
('FL041', 'British Airways', 'LHR', 'DXB', '2026-01-05 13:00:00', '2026-01-05 23:00:00', 650.00, 280, 250, 1.1),
('FL042', 'Emirates', 'DXB', 'LHR', '2026-01-06 02:00:00', '2026-01-06 07:00:00', 620.00, 280, 240, 1.1),
('FL043', 'Qatar Airways', 'DOH', 'BKK', '2026-01-07 08:00:00', '2026-01-07 19:00:00', 720.00, 300, 280, 1.0),
('FL044', 'Turkish Airlines', 'IST', 'JFK', '2026-01-08 10:00:00', '2026-01-08 13:00:00', 580.00, 250, 220, 1.2),
('FL045', 'Air India', 'DEL', 'LHR', '2026-01-09 14:00:00', '2026-01-09 18:00:00', 680.00, 270, 230, 1.1),
('FL046', 'Virgin Atlantic', 'LHR', 'JFK', '2026-01-10 16:00:00', '2026-01-10 19:00:00', 590.00, 200, 180, 1.1),
('FL047', 'Cathay Pacific', 'HKG', 'SYD', '2026-01-11 20:00:00', '2026-01-12 07:00:00', 850.00, 320, 300, 1.0),
('FL048', 'Japan Airlines', 'NRT', 'LAX', '2026-01-12 11:00:00', '2026-01-12 05:00:00', 920.00, 280, 260, 1.0),
('FL049', 'ANA', 'NRT', 'SIN', '2026-01-13 13:00:00', '2026-01-13 19:00:00', 480.00, 220, 200, 1.0),
('FL050', 'Korean Air', 'ICN', 'BKK', '2026-01-14 15:00:00', '2026-01-14 19:00:00', 320.00, 240, 220, 1.0);
-- Inserting DOMESTIC Flights (FL101 to FL130)
INSERT INTO flight (flight_number, airline, from_airport_code, to_airport_code, departure_time, arrival_time, base_price, total_seats, seats_remaining, demand_factor) VALUES
('FL101', 'IndiGo', 'DEL', 'BOM', '2025-12-10 06:00:00', '2025-12-10 08:15:00', 85.00, 180, 160, 1.0),
('FL102', 'Air India', 'BOM', 'DEL', '2025-12-10 09:30:00', '2025-12-10 11:45:00', 90.00, 180, 150, 1.0),
('FL103', 'SpiceJet', 'BLR', 'DEL', '2025-12-11 07:00:00', '2025-12-11 09:30:00', 95.00, 160, 140, 1.0),
('FL104', 'Vistara', 'DEL', 'BLR', '2025-12-11 10:30:00', '2025-12-11 13:00:00', 100.00, 160, 130, 1.0),
('FL105', 'IndiGo', 'MAA', 'DEL', '2025-12-12 08:00:00', '2025-12-12 10:45:00', 110.00, 170, 150, 1.0),
('FL106', 'Air India', 'DEL', 'MAA', '2025-12-12 11:30:00', '2025-12-12 14:15:00', 105.00, 170, 140, 1.0),
('FL107', 'SpiceJet', 'HYD', 'BOM', '2025-12-13 06:30:00', '2025-12-13 08:00:00', 70.00, 150, 130, 1.0),
('FL108', 'IndiGo', 'BOM', 'HYD', '2025-12-13 09:00:00', '2025-12-13 10:30:00', 75.00, 150, 120, 1.0),
('FL109', 'Vistara', 'CCU', 'DEL', '2025-12-14 07:30:00', '2025-12-14 10:00:00', 120.00, 160, 140, 1.0),
('FL110', 'Air India', 'DEL', 'CCU', '2025-12-14 11:00:00', '2025-12-14 13:30:00', 115.00, 160, 130, 1.0),
('FL111', 'IndiGo', 'BLR', 'MAA', '2025-12-15 06:00:00', '2025-12-15 07:00:00', 50.00, 120, 100, 1.0),
('FL112', 'SpiceJet', 'MAA', 'BLR', '2025-12-15 08:00:00', '2025-12-15 09:00:00', 55.00, 120, 90, 1.0),
('FL113', 'Air India', 'DEL', 'GOI', '2025-12-16 09:00:00', '2025-12-16 11:30:00', 130.00, 140, 120, 1.0),
('FL114', 'IndiGo', 'GOI', 'DEL', '2025-12-16 12:30:00', '2025-12-16 15:00:00', 125.00, 140, 110, 1.0),
('FL115', 'Vistara', 'BOM', 'JAI', '2025-12-17 07:00:00', '2025-12-17 08:30:00', 80.00, 130, 110, 1.0),
('FL116', 'IndiGo', 'JAI', 'BOM', '2025-12-17 09:30:00', '2025-12-17 11:00:00', 85.00, 130, 100, 1.0),
('FL117', 'SpiceJet', 'DEL', 'PAT', '2025-12-18 10:00:00', '2025-12-18 11:30:00', 95.00, 120, 100, 1.0),
('FL118', 'Air India', 'PAT', 'DEL', '2025-12-18 12:30:00', '2025-12-18 14:00:00', 90.00, 120, 90, 1.0),
('FL119', 'IndiGo', 'BLR', 'COK', '2025-12-19 13:00:00', '2025-12-19 14:30:00', 65.00, 110, 90, 1.0),
('FL120', 'SpiceJet', 'COK', 'BLR', '2025-12-19 15:30:00', '2025-12-19 17:00:00', 70.00, 110, 80, 1.0),
('FL121', 'Vistara', 'DEL', 'GAU', '2025-12-20 06:30:00', '2025-12-20 09:00:00', 140.00, 150, 130, 1.0),
('FL122', 'Air India', 'GAU', 'DEL', '2025-12-20 10:00:00', '2025-12-20 12:30:00', 135.00, 150, 120, 1.0),
('FL123', 'IndiGo', 'BOM', 'SXR', '2025-12-21 08:00:00', '2025-12-21 10:30:00', 150.00, 130, 110, 1.0),
('FL124', 'SpiceJet', 'SXR', 'BOM', '2025-12-21 11:30:00', '2025-12-21 14:00:00', 145.00, 130, 100, 1.0),
('FL125', 'Air India', 'DEL', 'PNQ', '2025-12-22 07:00:00', '2025-12-22 09:00:00', 110.00, 140, 120, 1.0),
('FL126', 'IndiGo', 'PNQ', 'DEL', '2025-12-22 10:00:00', '2025-12-22 12:00:00', 105.00, 140, 110, 1.0),
('FL127', 'Vistara', 'BLR', 'AMD', '2025-12-23 09:00:00', '2025-12-23 10:30:00', 85.00, 120, 100, 1.0),
('FL128', 'SpiceJet', 'AMD', 'BLR', '2025-12-23 11:30:00', '2025-12-23 13:00:00', 90.00, 120, 90, 1.0),
('FL129', 'IndiGo', 'HYD', 'LKO', '2025-12-24 12:00:00', '2025-12-24 14:00:00', 95.00, 130, 110, 1.0),
('FL130', 'Air India', 'LKO', 'HYD', '2025-12-24 15:00:00', '2025-12-24 17:00:00', 100.00, 130, 100, 1.0);


-- Bookings
INSERT INTO booking (user_id, flight_id, pnr, status, payment_reference) VALUES
(3, 1, 'PNR001XYZ', 'CONFIRMED', 'PAYMENT001'),
(4, 2, 'PNR002ABC', 'CONFIRMED', 'PAYMENT002');

-- Passengers
INSERT INTO passenger (booking_id, flight_id, seat_number, seat_type, full_name, age, contact_phone, contact_email) VALUES
(1, 1, '12A', 'WINDOW', 'Ali Hassan', 35, '9876543210', 'ali.hassan@example.com'),
(2, 2, '14B', 'AISLE', 'Jane Doe', 30, '1112223333', 'jane.doe@example.com');

-- Flight Price History
INSERT INTO flight_price_history (flight_id, recorded_price, demand_factor, seats_remaining) VALUES
(1, 550.00, 1.00, 200),
(2, 1200.50, 1.00, 300);

-- Payments
INSERT INTO payment (booking_id, payment_reference, payment_method, amount_paid, payment_status) VALUES
(1, 'PAYMENT001', 'CARD', 605.00, 'SUCCESS'),
(2, 'PAYMENT002', 'UPI', 1200.50, 'SUCCESS');

-- Cancelled Bookings
INSERT INTO cancelled_booking (pnr, user_id, flight_id, refund_amount, cancellation_reason, passenger_full_name) VALUES
('PNRCANCEL', 3, 2, 960.40, 'Change of plans', 'Ali Hassan');
