# PiStock — Installation on a Raspberry Pi

This guide gets a fresh PiStock server running on a Raspberry Pi (or any
Debian-based machine) and auto-starting on every boot, then points your
FreeCAD workstations at it.

> **Scope / security:** PiStock is meant for a **local network** (a
> workshop LAN). The whole web interface is behind an *access* password,
> and destructive actions need a separate *admin* password — but it is
> **not** hardened for exposure to the public Internet. Keep it on a
> trusted LAN.

---

## 1. Prerequisites

- A Raspberry Pi running Raspberry Pi OS (64-bit recommended) or any
  Debian/Ubuntu machine.
- Network access (Ethernet or Wi-Fi) on your LAN.
- `git` to clone the repository (the installer installs the rest).

---

## 2. Quick install (recommended)

Clone the repository and run the installer **as your normal user**
(not root — it calls `sudo` itself where needed):

```bash
git clone <repository-url> pistock
cd pistock
./deploy/install_pi.sh
```

That's it. At the end the script prints the **LAN IP and URL** you need.
Open `https://<PI_IP>:8000/` in a browser and accept the self-signed
certificate warning.

### What the installer does

| Step | Action |
|------|--------|
| 1 | Installs system packages: `python3-venv`, `git`, `openssl` |
| 2 | Creates a Python virtualenv `.venv` and installs `requirements.txt` |
| 3 | Initializes the database in `../data-pistock` (skipped if it exists) |
| 4 | Writes `pistock.conf` with the **auto-detected LAN IP** and port 8000 |
| 5 | Generates a self-signed TLS certificate (`key.pem`/`cert.pem`), once |
| 6 | Installs and enables a **systemd** service → autostart on every boot |
| 7 | Prints the IP/URL for the browser and the FreeCAD macros |

> The database and uploaded files live in **`../data-pistock`** (a folder
> *next to* the repository, e.g. `/home/pi/data-pistock`), so they are
> never touched by `git pull`.

You can override the port before running: `PISTOCK_PORT=8443 ./deploy/install_pi.sh`.

---

## 3. First run — passwords

PiStock has **two independent passwords**:

1. **Access password** (the front door). On your **first visit** to any
   page you are redirected to `/login` and asked to **create** it. It is
   then required to reach the interface. Stored in
   `../data-pistock/access_password.json`.
2. **Admin password** (destructive actions: deletions, unlocking parts,
   the *Database admin* plugin). Created the first time you trigger such
   an action. Stored in the database (`admin` table).

---

## 4. Point the FreeCAD macros at the server

The macros run on the **designer's FreeCAD workstation**, not on the Pi.
On each workstation:

```bash
cd backend/CAD-extensions/freecad
cp pistock_host.txt.example pistock_host.txt
```

Edit `pistock_host.txt` and put a single line with the Pi's IP (the one
the installer printed):

```
192.168.1.50
```

The macros will then talk to `https://192.168.1.50:8000`. Accepted
forms: `192.168.1.50`, `192.168.1.50:8000`, `pistock.local`, or a full
`https://host:port`.

---

## 5. Managing the service

```bash
sudo systemctl status pistock      # is it running?
sudo systemctl restart pistock     # restart (use after a 'git pull')
sudo systemctl stop pistock        # stop
sudo systemctl disable pistock     # don't start on boot anymore
journalctl -u pistock -f           # live logs
```

---

## 6. Updating PiStock

```bash
cd ~/pistock
git pull
source .venv/bin/activate
pip install -r requirements.txt    # only needed if dependencies changed
sudo systemctl restart pistock     # IMPORTANT: reload the new code
```

> Code changes only take effect **after a restart** of the service.

---

## 7. Backups

Use the built-in **Database admin** plugin (`/plugins` → *Database admin*,
admin password required):

- **Export** copies the whole `data-pistock` (database + files) to a
  target folder — including an external disk mounted on the Pi (e.g.
  under `/media/...`). Use the graphical folder picker.
- **Import** restores another export (it backs up the current data first).
- **Merge** integrates another database into the current one (e.g. parts
  added while working on a USB stick).

Since there are no automatic schema migrations yet, **export before any
upgrade** that might change the database schema.

---

## 8. Forgotten password / recovery

Both resets are done on the server (hence: physical/SSH access to the Pi
is the real root of trust) and **lose no data** — they only clear a
password.

| Forgot the… | Run on the Pi | Effect |
|-------------|---------------|--------|
| **Access** password | `rm ../data-pistock/access_password.json` | `/login` offers to create it again |
| **Admin** password | `sqlite3 ../data-pistock/pistockdatabase.sqlite3 "DELETE FROM admin;"` | next admin action offers to create it again |

(Paths are relative to the repository root.)

---

## 9. Manual install (without the script)

```bash
git clone <repository-url> pistock && cd pistock
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install -r requirements.txt
python backend/app/install/init_db.py          # creates ../data-pistock
cp pistock.conf.example pistock.conf            # then edit PISTOCK_DIR / PISTOCK_IP / PISTOCK_PORT
bash backend/app/startapp_newssl.sh             # generates the cert + runs (HTTPS)
```

For autostart, replicate the systemd unit created by the installer
(see `deploy/install_pi.sh`, step 6).

---

## 10. Troubleshooting

- **The page keeps redirecting to /login after I log in:** make sure the
  running server is the current code — `sudo systemctl restart pistock`.
  Code changes are not picked up until restart.
- **Wrong IP detected:** edit `PISTOCK_IP` in `pistock.conf`, regenerate
  the certificate (`bash backend/app/startapp_newssl.sh`, then Ctrl-C),
  and `sudo systemctl restart pistock`.
- **Port already in use / change port:** edit `PISTOCK_PORT` in
  `pistock.conf`, then restart the service.
- **A dependency tries to compile from source** (rare on 64-bit Pi OS):
  `sudo apt-get install -y build-essential python3-dev libssl-dev`, then
  re-run `pip install -r requirements.txt`.
- **Certificate warning in the browser:** expected — it's a self-signed
  certificate for your LAN. Accept it once.
- **Restrict access to the workshop subnet (optional hardening):**
  `sudo ufw allow from 192.168.1.0/24 to any port 8000`.
