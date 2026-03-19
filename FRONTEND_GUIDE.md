# Modern UI Guide for Automation Framework

## Overview

This document explains how to use the modern React-based UI for the AI-Powered Record and Playback Automation Framework. The UI provides an intuitive, interactive dashboard at `/record-and-play` that allows stakeholders to easily manage all automation activities.

## Accessing the UI

Once the backend is running, access the UI at:
```
http://localhost:8000/record-and-play
```

The API documentation is available at:
```
http://localhost:8000/docs
```

## Main Dashboard Features

### 1. Recording Dashboard
- **Start/Stop Recording**: Begin recording user interactions with visual indicators
- **Session Management**: Name and manage recording sessions
- **Real-time Statistics**: View current automation metrics
- **Recent Flows**: Quick access to recently recorded flows

### 2. Flow Management
- **View All Flows**: Comprehensive table view of all recorded flows
- **Sort and Filter**: Organize flows by name, date, status, or steps
- **Flow Actions**:
  - View detailed flow information
  - Edit flow metadata
  - Duplicate flows for variations
  - Delete unwanted flows
  - Play flows directly from the interface

### 3. Playback Control
- **Playback Modes**: Choose between Exact Replay, AI-Enhanced, Hybrid, or Standard Data modes
- **Progress Tracking**: Visual progress indicator during execution
- **Execution Logs**: Real-time logging of playback steps
- **Performance Statistics**: Monitor success rates and execution times

### 4. AI Data Generation
- **Contextual Data Creation**: Generate test data that maintains field relationships
- **Pattern-Based Generation**: Use recorded data as templates for new data
- **Bulk Generation**: Create multiple records at once
- **Export Options**: Download generated data as JSON

### 5. Configuration Panel
- **Centralized Settings**: Manage all framework configuration in one place
- **Section Organization**:
  - Application URLs
  - Authentication Credentials
  - Standard Test Data
  - AI Model Settings
  - Playback Settings
  - Data Generation Settings
- **Connection Testing**: Verify URL accessibility directly from the UI

## Stakeholder Benefits

### For QA Engineers:
- Easy recording and playback without technical setup
- Quick access to test data generation
- Immediate visibility into test execution results

### For Product Managers:
- Clear overview of automation coverage
- Ability to trigger specific flows for demos
- Insight into test success/failure rates

### For Developers:
- Direct API interaction through intuitive UI
- Configuration management without manual file editing
- Real-time monitoring of automation activities

## Getting Started Guide

### 1. Initial Setup
1. Start the backend server: `python backend/main.py`
2. Access the UI at `http://localhost:8000/record-and-play`
3. Navigate through the sidebar menu to access different features

### 2. Recording a New Flow
1. Go to the Dashboard or Recording section
2. Enter a session name
3. Click "Start Recording"
4. Perform the actions you want to automate
5. Click "Stop Recording" when finished

### 3. Managing Flows
1. Visit the Flow Management section
2. View all recorded flows in the table
3. Use action buttons to view, edit, duplicate, or delete flows
4. Play flows directly from the interface

### 4. Executing Playback
1. Go to the Playback Control section
2. Select a flow from the dropdown
3. Choose a playback mode
4. Click "Start Playback"
5. Monitor progress and view execution logs

### 5. Generating Test Data
1. Visit the AI Data Generation section
2. Review the original data pattern
3. Select the number of records to generate
4. Click "Generate Data"
5. Download or copy the generated data

### 6. Configuring Settings
1. Go to the Configuration section
2. Expand the relevant configuration section
3. Modify settings as needed
4. Click "Save Configuration"
5. Use "Test" buttons to verify URL connectivity

## Technical Implementation

### Frontend Stack
- **Framework**: React 18 with Hooks
- **UI Library**: Ant Design (AntD)
- **State Management**: React built-in state and effects
- **Routing**: React Router v6
- **API Communication**: Axios
- **Data Visualization**: react-json-view

### Backend Integration
- RESTful API endpoints for all operations
- JSON data format for all communications
- Static file serving for production builds
- CORS handling for development

### Responsive Design
- Adapts to different screen sizes
- Mobile-friendly navigation
- Accessible UI components
- Keyboard navigation support

## Best Practices

### For Recording
- Give sessions descriptive names
- Record complete user journeys
- Minimize unnecessary steps
- Test playback immediately after recording

### For Flow Management
- Regularly review and clean up old flows
- Use descriptive names and descriptions
- Duplicate flows before making major changes
- Organize flows by feature or functionality

### For Playback
- Start with Exact Replay mode for verification
- Use AI-Enhanced mode for varied test data
- Monitor execution logs for failures
- Adjust playback settings for optimal performance

### For Configuration
- Regular backup of configuration settings
- Use environment-specific configurations
- Test connections after URL changes
- Document configuration changes for team reference

## Troubleshooting

### Common Issues
1. **UI Not Loading**: Ensure backend is running and accessible
2. **API Errors**: Check backend console for error messages
3. **Recording Not Working**: Verify browser compatibility and permissions
4. **Playback Failures**: Check element selectors and timing settings

### Support Resources
- API Documentation: `/docs` endpoint
- Framework Documentation: README.md
- Issue Tracking: GitHub Issues (if applicable)

## Feedback and Improvements

We welcome feedback on the UI to improve usability and functionality. Please report any issues or suggestions through the appropriate channels.