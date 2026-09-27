import sys
from unittest.mock import patch, MagicMock

# Define dummy tmdb_get before importing main, or mock it inside the patch
with patch('main.TMDB_API_KEY', 'dummy_key'):
    import main

def test_tvmaze_mapping():
    print("Testing Ted Lasso mapping (should be streaming-only, so is_network=False)...")
    # Mock TMDB external_ids response for Ted Lasso (show_id 97546)
    # Ted Lasso IMDb: tt10986410
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.return_value = {"imdb_id": "tt10986410"}
        mapping = main.get_tvmaze_episode_mapping(97546, "Ted Lasso")
        
        # Check that we found some episodes and is_network is False
        print(f"Ted Lasso episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is False, "Ted Lasso should NOT be a network show"
            print("✓ Ted Lasso test passed!")
        else:
            print("✗ Ted Lasso returned no episodes")

    print("\nTesting Saturday Night Live mapping (should be network, so is_network=True)...")
    # Mock TMDB external_ids response for Saturday Night Live (show_id 1667)
    # SNL IMDb: tt0072562
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.return_value = {"imdb_id": "tt0072562"}
        mapping = main.get_tvmaze_episode_mapping(1667, "Saturday Night Live")
        
        print(f"SNL episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is True, "SNL should be a network show"
            assert sample_val["airstamp"] is not None, "SNL should have an airstamp"
            print("✓ SNL test passed!")
        else:
            print("✗ SNL returned no episodes")

    print("\nTesting fallback to single search by name (when TMDB external IDs lookup fails/empty)...")
    with patch('main.tmdb_get') as mock_tmdb_get:
        mock_tmdb_get.side_effect = Exception("TMDB down")
        mapping = main.get_tvmaze_episode_mapping(60625, "Rick and Morty")
        
        print(f"Rick and Morty episodes fetched: {len(mapping)}")
        if mapping:
            sample_key = list(mapping.keys())[0]
            sample_val = mapping[sample_key]
            print(f"Sample episode {sample_key}: {sample_val}")
            assert sample_val["is_network"] is True, "Rick and Morty should be a network show"
            print("✓ Rick and Morty fallback test passed!")
        else:
            print("✗ Rick and Morty returned no episodes")

if __name__ == "__main__":
    test_tvmaze_mapping()
