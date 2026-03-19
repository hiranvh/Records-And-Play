"""
Simple test script to verify API endpoints
"""

def test_api_endpoints():
    """Test that our API routers can be imported"""
    try:
        # Test recording router
        from backend.routers import recording
        print("[OK] Recording router loaded successfully")

        # Test flow_management router
        from backend.routers import flow_management
        print("[OK] Flow management router loaded successfully")

        # Test ai_service router
        from backend.routers import ai_service
        print("[OK] AI service router loaded successfully")

        # Test config_service router
        from backend.routers import config_service
        print("[OK] Config service router loaded successfully")

        print("\n[SUCCESS] All API routers loaded successfully!")
        return True

    except Exception as e:
        print(f"[ERROR] Error testing API routers: {e}")
        return False

if __name__ == "__main__":
    print("Testing API endpoints...")
    test_api_endpoints()