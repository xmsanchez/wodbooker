#!/usr/bin/env python3
"""
Generate VAPID keys for Web Push Notifications

This script generates VAPID (Voluntary Application Server Identification) keys
required for Web Push API notifications.

Usage:
    python generate_vapid_keys.py [email]

The script will output:
    - VAPID_PUBLIC_KEY: Set this as an environment variable
    - VAPID_PRIVATE_KEY: Set this as an environment variable (keep it secret!)
    - VAPID_CLAIM_EMAIL: Optional, defaults to mailto:admin@example.com
"""

import sys
import os

# Try to use py_vapid first (recommended)
try:
    from py_vapid import Vapid01
    USE_PY_VAPID = True
except ImportError:
    USE_PY_VAPID = False
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.backends import default_backend
        import base64
        USE_CRYPTOGRAPHY = True
    except ImportError:
        USE_CRYPTOGRAPHY = False
        print("Error: Neither py-vapid nor cryptography is installed.")
        print("Please install one of them:")
        print("  pip install py-vapid")
        print("  OR")
        print("  pip install cryptography")
        sys.exit(1)

def generate_vapid_keys_pyvapid():
    """Generate VAPID keys using py_vapid (recommended method)"""
    vapid = Vapid01()
    vapid.generate_keys()
    
    # Get keys in the format pywebpush expects
    public_key = vapid.public_key.public_bytes_raw
    private_key = vapid.private_key.private_bytes_raw
    
    # Convert to base64url format
    import base64
    public_key_b64 = base64.urlsafe_b64encode(public_key).decode('utf-8').rstrip('=')
    private_key_b64 = base64.urlsafe_b64encode(private_key).decode('utf-8').rstrip('=')
    
    return public_key_b64, private_key_b64

def generate_vapid_keys_cryptography():
    """Generate VAPID keys using cryptography library (fallback)"""
    # Generate a new EC private key using P-256 curve
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()
    
    # Get the raw public key bytes (uncompressed point: 0x04 + X + Y)
    public_key_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint
    )
    
    # Get the raw private key (32 bytes)
    private_key_bytes = private_key.private_numbers().private_value.to_bytes(32, 'big')
    
    # Convert to base64url format
    public_key_b64 = base64.urlsafe_b64encode(public_key_bytes).decode('utf-8').rstrip('=')
    private_key_b64 = base64.urlsafe_b64encode(private_key_bytes).decode('utf-8').rstrip('=')
    
    return public_key_b64, private_key_b64

def generate_vapid_keys(email='mailto:admin@example.com'):
    """
    Generate VAPID keys
    
    Args:
        email: Email address for VAPID claim (format: mailto:email@example.com)
    
    Returns:
        tuple: (public_key, private_key) in base64url format
    """
    if USE_PY_VAPID:
        return generate_vapid_keys_pyvapid()
    elif USE_CRYPTOGRAPHY:
        from cryptography.hazmat.primitives import serialization
        return generate_vapid_keys_cryptography()
    else:
        raise RuntimeError("No VAPID key generation method available")

if __name__ == '__main__':
    print("=" * 60)
    print("VAPID Key Generator for Web Push Notifications")
    print("=" * 60)
    print()
    
    # Get email from command line or use default
    email = sys.argv[1] if len(sys.argv) > 1 else 'mailto:admin@example.com'
    if not email.startswith('mailto:'):
        email = f'mailto:{email}'
    
    try:
        public_key, private_key = generate_vapid_keys(email)
        
        print("✓ VAPID keys generated successfully!")
        print()
        print("Add these to your environment variables:")
        print("-" * 60)
        print(f"export VAPID_PUBLIC_KEY='{public_key}'")
        print(f"export VAPID_PRIVATE_KEY='{private_key}'")
        print(f"export VAPID_CLAIM_EMAIL='{email}'")
        print("-" * 60)
        print()
        print("Or add them to your .env file:")
        print("-" * 60)
        print(f"VAPID_PUBLIC_KEY={public_key}")
        print(f"VAPID_PRIVATE_KEY={private_key}")
        print(f"VAPID_CLAIM_EMAIL={email}")
        print("-" * 60)
        print()
        print("⚠️  IMPORTANT: Keep the private key secret! Do not commit it to version control.")
        print()
        
    except Exception as e:
        print(f"Error generating VAPID keys: {e}")
        sys.exit(1)

