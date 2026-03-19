class RecorderService:
    """Service to handle recording of user interactions"""

    def capture_element(self, element):
        """
        Capture element selector information

        Args:
            element: The DOM element to capture
        """
        # In a real implementation, this would use Playwright to capture element info
        selector_info = {
            "id": element.get_attribute("id") if element.get_attribute("id") else None,
            "class": element.get_attribute("class") if element.get_attribute("class") else None,
            "tag": element.tag_name,
            "xpath": self._generate_xpath(element)
        }
        return selector_info

    def _generate_xpath(self, element):
        """
        Generate XPath for an element
        This is a simplified version - in practice, this would be more robust
        """
        # Simplified XPath generation
        return f"//{element.tag_name}[@id='{element.get_attribute('id')}']" if element.get_attribute("id") else f"//{element.tag_name}"

    def record_action(self, action_type, element, value=None):
        """
        Record a user action

        Args:
            action_type: Type of action (click, input, select, etc.)
            element: The DOM element the action was performed on
            value: Value associated with the action (for input, select)
        """
        element_info = self.capture_element(element)

        step = {
            "type": action_type,
            "selector": element_info,
            "timestamp": self._get_timestamp(),
            "value": value
        }

        # Add field name if available for data generation
        if element.get_attribute("name"):
            step["field_name"] = element.get_attribute("name")

        return step

    def _get_timestamp(self):
        """Get current timestamp"""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"