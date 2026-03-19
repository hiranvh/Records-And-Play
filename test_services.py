"""
Simple test script to verify core functionality
"""

def test_services():
    """Test that our service classes can be imported and instantiated"""
    try:
        # Test ConfigService
        from backend.services.config_service import ConfigService
        config_service = ConfigService()
        print("[OK] ConfigService loaded successfully")

        # Test a simple config read
        base_url = config_service.get_property("base.url")
        print(f"[OK] Config read test - Base URL: {base_url}")

        # Test FlowService
        from backend.services.flow_service import FlowService
        flow_service = FlowService()
        print("[OK] FlowService loaded successfully")

        # Test RecorderService
        from backend.services.recorder_service import RecorderService
        recorder_service = RecorderService()
        print("[OK] RecorderService loaded successfully")

        # Test PlayerService
        from backend.services.player_service import PlayerService
        player_service = PlayerService()
        print("[OK] PlayerService loaded successfully")

        # Test LLMService
        from backend.services.llm_service import LLMService
        llm_service = LLMService()
        print("[OK] LLMService loaded successfully")

        print("\n[SUCCESS] All services loaded successfully!")
        return True

    except Exception as e:
        print(f"[ERROR] Error testing services: {e}")
        return False

if __name__ == "__main__":
    print("Testing core services...")
    test_services()