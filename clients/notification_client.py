import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from rich.console import Console

import config

console = Console()

def send_email(to_email: str, subject: str, html_body: str):
    """
    Sends an HTML email using SMTP configuration in .env.
    """
    if not to_email:
        to_email = config.SENDER_EMAIL or config.SMTP_USER
        
    if not config.SMTP_SERVER or not config.SMTP_USER or not config.SMTP_PASS or not to_email:
        console.print(f"[dim][Notification Client] Mock Email to {to_email}: {subject}[/dim]")
        return
        
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config.SENDER_EMAIL or config.SMTP_USER
        msg["To"] = to_email
        
        # Add a plain text fallback to prevent Gmail from flagging as spam
        text_part = MIMEText("Please view this email in an HTML-compatible email client to see the Prescriptive Recommendations.", "plain")
        html_part = MIMEText(html_body, "html")
        
        msg.attach(text_part)
        msg.attach(html_part)
        
        # Connect to SMTP server
        server = smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT)
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(msg["From"], [msg["To"]], msg.as_string())
        server.quit()
        
        console.print(f"[bold green][Notification Client] Email successfully sent to {to_email}[/bold green]")
        
    except Exception as e:
        console.print(f"[bold red][Notification Client] Failed to send email to {to_email}: {e}[/bold red]")


def send_teams_message(markdown_body: str):
    """
    Sends a message to Microsoft Teams via an Incoming Webhook.
    """
    webhook_url = config.TEAMS_WEBHOOK_URL
    if not webhook_url or webhook_url.endswith("..."):
        console.print("[dim][Notification Client] Mock Teams Message Sent.[/dim]")
        return
        
    try:
        # Microsoft Teams simple text payload (supports Markdown)
        payload = {
            "text": markdown_body
        }
        
        response = requests.post(webhook_url, json=payload, headers={'Content-Type': 'application/json'}, timeout=10)
        response.raise_for_status()
        
        console.print("[bold green][Notification Client] Message successfully pushed to MS Teams![/bold green]")
        
    except Exception as e:
        console.print(f"[bold red][Notification Client] Failed to send Teams message: {e}[/bold red]")
